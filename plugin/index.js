/**
 * AegisRoute OmniRoute Plugin
 * 
 * Provides:
 * - Dynamic Colab-LLM provider registration (colab-aegis)
 * - Real-time tunnel health monitoring & hot URL updates
 * - Task-based security prompt interception & routing (0xalpha Security Model)
 * - Circuit breaker with automated Playwright recovery & quota cooldown
 * - Standard OmniRoute Hook lifecycle (onRequest, onResponse, onError, onActivate, onDeactivate)
 * - CLI subcommands for omniroute CLI integration
 */

const http = typeof require !== 'undefined' ? require('http') : null;
const https = typeof require !== 'undefined' ? require('https') : null;
const { spawn } = typeof require !== 'undefined' ? require('child_process') : {};
const path = typeof require !== 'undefined' ? require('path') : {};

const PROVIDER_ID = 'colab-aegis';

class AegisRoutePlugin {
  constructor(config = {}) {
    this.name = 'omniroute-plugin-aegis';
    this.version = '1.0.0';
    
    // Configuration with sensible defaults
    this.config = {
      enabled: config.enabled !== false,
      tunnelUrl: config.tunnelUrl || (typeof process !== 'undefined' && process.env ? process.env.AEGIS_TUNNEL_URL : '') || 'http://localhost:8000/v1',
      colabNotebookUrl: config.colabNotebookUrl || (typeof process !== 'undefined' && process.env ? process.env.AEGIS_COLAB_URL : '') || '',
      healthCheckIntervalSeconds: config.healthCheckIntervalSeconds || 30,
      cooldownHours: config.cooldownHours || 4.0,
      fallbackChain: config.fallbackChain || ['local-mlx', 'anthropic', 'openai'],
      securityKeywords: config.securityKeywords || [
        'audit',
        'vulnerability',
        'reentrancy',
        'exploit',
        'smart contract',
        'cve',
        'overflow',
        'injection',
        'privilege escalation',
        'bypass',
        'malware',
        'strix',
        'zero-day',
        'ast-grep',
        'semgrep'
      ],
      alertChannels: config.alertChannels || {
        discordWebhookUrl: (typeof process !== 'undefined' && process.env ? process.env.AEGIS_DISCORD_WEBHOOK_URL : '') || '',
        n8nWebhookUrl: (typeof process !== 'undefined' && process.env ? process.env.AEGIS_N8N_WEBHOOK_URL : '') || '',
        enableMacosNotification: true,
      },
    };

    // Compile regex pattern for fast security keyword matching
    const escapedKeywords = this.config.securityKeywords.map(kw => kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    this.securityRegex = new RegExp(`\\b(${escapedKeywords.join('|')})\\b`, 'i');

    // Runtime state
    this.state = {
      status: 'INITIALIZING', // HEALTHY | DEGRADED | COOLDOWN | INITIALIZING
      disabledUntil: null,
      lastHealthCheck: null,
      activeModel: '0xalpha/Security-Audit-7B-GGUF',
      metrics: {
        totalRequests: 0,
        securityRouted: 0,
        fallbacksTriggered: 0,
        lastError: null,
      },
    };

    this.healthIntervalId = null;
    this.isRecovering = false;
  }

  log(msg, level = 'INFO') {
    const ts = new Date().toISOString();
    console.log(`[${ts}] [${level}] [OmniRoute:AegisPlugin] ${msg}`);
  }

  /**
   * OmniRoute Lifecycle Hook: onInit / onActivate
   */
  async onInit(context = {}) {
    this.log(`Initializing AegisRoute Plugin v${this.version}...`);
    this.context = context;

    // Register provider into OmniRoute registry if available
    this.registerProvider(this.config.tunnelUrl);

    // Register Admin Webhook endpoints if router is available
    if (context && context.registerAdminRoute) {
      context.registerAdminRoute('POST', '/aegis/update-tunnel', (req, res) => this.handleUpdateTunnel(req, res));
      context.registerAdminRoute('GET', '/aegis/status', (req, res) => this.handleGetStatus(req, res));
      context.registerAdminRoute('POST', '/aegis/trigger-recover', (req, res) => this.handleTriggerRecover(req, res));
    }

    // Start background health checking
    this.startHealthCheck();
    this.log(`Provider '${PROVIDER_ID}' registered targeting: ${this.config.tunnelUrl}`);
    return true;
  }

  /**
   * OmniRoute Lifecycle Hook: onDestroy / onDeactivate
   */
  async onDestroy() {
    this.log('Cleaning up AegisRoute Plugin resources...');
    if (this.healthIntervalId) {
      clearInterval(this.healthIntervalId);
      this.healthIntervalId = null;
    }
  }

  /**
   * Register or update the colab-aegis provider definition in OmniRoute
   */
  registerProvider(baseUrl) {
    const cleanUrl = baseUrl.replace(/\/v1\/?$/, '') + '/v1';
    this.config.tunnelUrl = cleanUrl;

    const providerDef = {
      id: PROVIDER_ID,
      name: 'Google Colab Aegis Bridge',
      type: 'openai-compatible',
      baseUrl: cleanUrl,
      apiKey: 'none',
      models: [
        { id: 'aegis-security', name: 'Aegis 0xalpha Security Audit 7B', contextWindow: 8192 },
        { id: 'aegis-coder', name: 'Aegis Qwen 2.5 Coder', contextWindow: 8192 },
        { id: 'aegis-uncensored', name: 'Aegis Qwen 3.8 27B Uncensored', contextWindow: 8192 },
      ],
      isDynamic: true,
      status: this.state.status,
    };

    if (this.context && this.context.providers && typeof this.context.providers.register === 'function') {
      this.context.providers.register(providerDef);
    }
  }

  /**
   * Start periodic health-check against /v1/models
   */
  startHealthCheck() {
    if (this.healthIntervalId) {
      clearInterval(this.healthIntervalId);
    }

    const check = async () => {
      // If currently in cooldown, check if cooldown has expired
      if (this.state.disabledUntil) {
        if (Date.now() < this.state.disabledUntil) {
          const remainingMinutes = Math.round((this.state.disabledUntil - Date.now()) / 60000);
          this.log(`Provider in GPU Quota Cooldown. Remaining: ${remainingMinutes} minutes.`, 'WARN');
          this.state.status = 'COOLDOWN';
          return;
        } else {
          this.log('GPU Quota Cooldown expired. Resuming health checks...', 'INFO');
          this.state.disabledUntil = null;
        }
      }

      const isHealthy = await this.pingEndpoint(this.config.tunnelUrl);
      this.state.lastHealthCheck = new Date().toISOString();

      if (isHealthy) {
        if (this.state.status !== 'HEALTHY') {
          this.log(`Inference Endpoint is back online: ${this.config.tunnelUrl}`, 'INFO');
          this.state.status = 'HEALTHY';
        }
      } else {
        if (this.state.status === 'HEALTHY') {
          this.log(`Inference Endpoint ping failed! Marking DEGRADED: ${this.config.tunnelUrl}`, 'WARN');
          this.state.status = 'DEGRADED';
        }
      }
    };

    // Run first check immediately
    check();
    this.healthIntervalId = setInterval(check, this.config.healthCheckIntervalSeconds * 1000);
  }

  /**
   * HTTP ping /v1/models endpoint
   */
  async pingEndpoint(baseUrl) {
    if (typeof fetch === 'function') {
      try {
        const urlStr = `${baseUrl.replace(/\/+$/, '')}/models`;
        const res = await fetch(urlStr, { method: 'GET', signal: AbortSignal.timeout(6000) });
        return res.status >= 200 && res.status < 300;
      } catch (e) {
        return false;
      }
    }

    return new Promise((resolve) => {
      try {
        const urlStr = `${baseUrl.replace(/\/+$/, '')}/models`;
        const url = new URL(urlStr);
        const transport = url.protocol === 'https:' ? https : http;

        if (!transport) {
          return resolve(false);
        }

        const req = transport.get(url, { timeout: 6000 }, (res) => {
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve(true);
          } else {
            resolve(false);
          }
        });

        req.on('error', () => resolve(false));
        req.on('timeout', () => {
          req.destroy();
          resolve(false);
        });
      } catch (err) {
        resolve(false);
      }
    });
  }

  /**
   * OmniRoute Lifecycle Hook: onRoute
   * Intercepts and routes security-sensitive requests
   */
  async onRoute(requestContext) {
    this.state.metrics.totalRequests++;

    const messages = requestContext.messages || (requestContext.body && requestContext.body.messages) || [];
    let fullPromptText = '';

    for (const msg of messages) {
      if (typeof msg.content === 'string') {
        fullPromptText += ' ' + msg.content;
      } else if (Array.isArray(msg.content)) {
        fullPromptText += ' ' + msg.content.map(c => c.text || '').join(' ');
      }
    }

    const isSecurityAudit = this.securityRegex.test(fullPromptText);

    // If prompt contains security keywords and provider is healthy, route to colab-aegis
    if (isSecurityAudit) {
      this.state.metrics.securityRouted++;
      this.log(`Security keyword detected in prompt. Prioritizing '${PROVIDER_ID}' (0xalpha model).`);

      if (this.state.status === 'HEALTHY') {
        return {
          providerId: PROVIDER_ID,
          modelId: 'aegis-security',
          overrides: {
            temperature: 0.1, // Deterministic for security auditing
          },
        };
      } else {
        this.log(`Target provider '${PROVIDER_ID}' is ${this.state.status}. Routing to fallback chain.`, 'WARN');
        return this.getNextFallbackRoute(requestContext);
      }
    }

    // Default passthrough or standard routing
    return null;
  }

  /**
   * Standard OmniRoute Middleware Hook: onRequest
   */
  async onRequest(ctx) {
    if (!this.config.enabled || !ctx) return;
    if (ctx.metadata && ctx.metadata.aegisProcessed) return;

    if (ctx.metadata) {
      ctx.metadata.aegisProcessed = true;
    }

    this.state.metrics.totalRequests++;

    const body = ctx.body;
    if (!body) return;

    const messages = body.messages || [];
    let fullPromptText = '';

    for (const msg of messages) {
      if (!msg) continue;
      if (typeof msg.content === 'string') {
        fullPromptText += ' ' + msg.content;
      } else if (Array.isArray(msg.content)) {
        fullPromptText += ' ' + msg.content.map(c => (c && c.text) || '').join(' ');
      }
    }

    const isSecurityAudit = this.securityRegex.test(fullPromptText);
    if (isSecurityAudit) {
      this.state.metrics.securityRouted++;
      this.log(`Security keyword detected in onRequest hook. Prioritizing Aegis.`);

      if (ctx.metadata) {
        ctx.metadata.aegisSecurityRouted = true;
        ctx.metadata.aegisTargetProvider = PROVIDER_ID;
      }
    }
  }

  /**
   * Standard OmniRoute Middleware Hook: onResponse
   */
  async onResponse(ctx, response) {
    return response;
  }

  /**
   * Standard OmniRoute Middleware Hook: onError
   */
  async onError(ctx, error) {
    if (!this.config.enabled) return;
    return this.onFallback(error, ctx);
  }

  /**
   * OmniRoute Lifecycle Hook: onFallback
   * Invoked when colab-aegis provider fails during request execution
   */
  async onFallback(error, requestContext = {}) {
    this.state.metrics.fallbacksTriggered++;
    this.state.metrics.lastError = error ? error.message : 'Unknown Failure';
    this.log(`Colab endpoint failure detected during inference: ${this.state.metrics.lastError}`, 'ERROR');

    // Trigger Playwright diagnostic / auto-recovery in background
    this.triggerAutoRecovery();

    // Pick immediate fallback provider
    return this.getNextFallbackRoute(requestContext);
  }

  /**
   * Determine next available fallback route from fallbackChain
   */
  getNextFallbackRoute(requestContext = {}) {
    for (const providerId of this.config.fallbackChain) {
      if (providerId !== PROVIDER_ID) {
        this.log(`Redirecting request to fallback provider: '${providerId}'`);
        return {
          providerId: providerId,
          modelId: requestContext.model || 'default',
        };
      }
    }
    return null;
  }

  /**
   * Execute Playwright Controller to diagnose or recover Colab runtime
   */
  triggerAutoRecovery() {
    if (this.isRecovering) {
      this.log('Recovery already in progress. Skipping redundant launch.', 'WARN');
      return;
    }

    this.isRecovering = true;
    this.log('Launching Playwright Colab Controller to diagnose/recover runtime...');

    const baseDir = typeof __dirname !== 'undefined' ? __dirname : '.';
    const scriptPath = path.resolve ? path.resolve(baseDir, '../core/playwright_controller.py') : 'core/playwright_controller.py';
    const pythonExe = (typeof process !== 'undefined' && process.env && process.env.PYTHON_PATH) || 'python3';

    if (!spawn) {
      this.log('spawn child_process unavailable in this sandbox mode.', 'WARN');
      this.isRecovering = false;
      return;
    }

    const args = [
      scriptPath,
      '--timeout', '300',
    ];

    if (this.config.colabNotebookUrl) {
      args.push('--url', this.config.colabNotebookUrl);
    }

    try {
      const proc = spawn(pythonExe, args, {
        cwd: path.resolve ? path.resolve(baseDir, '..') : '.',
        env: typeof process !== 'undefined' ? process.env : {},
      });

      let output = '';
      if (proc.stdout) proc.stdout.on('data', (d) => { output += d.toString(); });
      if (proc.stderr) proc.stderr.on('data', (d) => { output += d.toString(); });

      proc.on('close', (code) => {
        this.isRecovering = false;
        this.log(`Playwright controller finished with exit code: ${code}`);

        if (code === 2) {
          // Exit Code 2 = GPU Quota Limit Exceeded
          const cooldownMs = this.config.cooldownHours * 3600 * 1000;
          this.state.disabledUntil = Date.now() + cooldownMs;
          this.state.status = 'COOLDOWN';
          this.log(`GPU Quota limit reached. Setting ${this.config.cooldownHours}h cooldown.`, 'ERROR');
          this.dispatchAlert('Colab GPU Quota Limit', `GPU units exhausted. Cooldown active for ${this.config.cooldownHours} hours.`);
        } else if (code === 0) {
          // Exit Code 0 = Successfully booted, check if new URL was emitted
          const match = output.match(/AEGIS_TUNNEL_URL=(https:\/\/[^\s]+)/);
          if (match && match[1]) {
            this.updateTunnelUrl(match[1]);
          }
          this.state.status = 'HEALTHY';
          this.state.disabledUntil = null;
        } else {
          this.state.status = 'DEGRADED';
        }
      });
    } catch (err) {
      this.isRecovering = false;
      this.log(`Failed to spawn Playwright recovery: ${err.message}`, 'ERROR');
    }
  }

  /**
   * Update active tunnel URL on the fly
   */
  updateTunnelUrl(newUrl) {
    const cleanUrl = newUrl.replace(/\/+$/, '');
    const finalUrl = cleanUrl.endsWith('/v1') ? cleanUrl : `${cleanUrl}/v1`;
    this.log(`Live updating tunnel URL: ${this.config.tunnelUrl} -> ${finalUrl}`);
    this.config.tunnelUrl = finalUrl;
    this.registerProvider(finalUrl);
    this.state.status = 'HEALTHY';
    this.state.disabledUntil = null;
  }

  /**
   * Admin Endpoint: POST /aegis/update-tunnel
   */
  handleUpdateTunnel(req, res) {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      try {
        const payload = JSON.parse(body || '{}');
        const newUrl = payload.tunnel_url || payload.tunnelUrl;

        if (!newUrl) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          return res.end(JSON.stringify({ error: 'Missing tunnel_url in JSON payload' }));
        }

        this.updateTunnelUrl(newUrl);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: true, active_tunnel_url: this.config.tunnelUrl, status: this.state.status }));
      } catch (err) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: err.message }));
      }
    });
  }

  /**
   * Admin Endpoint: GET /aegis/status
   */
  handleGetStatus(req, res) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      plugin: this.name,
      version: this.version,
      state: this.state,
      config: {
        tunnelUrl: this.config.tunnelUrl,
        fallbackChain: this.config.fallbackChain,
        cooldownHours: this.config.cooldownHours,
      },
    }, null, 2));
  }

  /**
   * Admin Endpoint: POST /aegis/trigger-recover
   */
  handleTriggerRecover(req, res) {
    this.triggerAutoRecovery();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ message: 'Playwright recovery dispatched' }));
  }

  /**
   * Dispatch alerts across notification channels
   */
  dispatchAlert(title, message) {
    if (!spawn) return;
    const baseDir = typeof __dirname !== 'undefined' ? __dirname : '.';
    const scriptPath = path.resolve ? path.resolve(baseDir, '../core/alerting.py') : 'core/alerting.py';
    const pythonExe = (typeof process !== 'undefined' && process.env && process.env.PYTHON_PATH) || 'python3';
    try {
      spawn(pythonExe, [scriptPath], {
        cwd: path.resolve ? path.resolve(baseDir, '..') : '.',
        env: Object.assign({}, typeof process !== 'undefined' ? process.env : {}, {
          AEGIS_ALERT_TITLE: title,
          AEGIS_ALERT_MSG: message,
        }),
        detached: true,
      }).unref();
    } catch (e) {}
  }
}

// Instantiate default singleton instance
const defaultInstance = new AegisRoutePlugin();

// Clean, non-circular exports conforming to OmniRoute Plugin API
module.exports = {
  meta: {
    name: "aegis",
    version: "1.0.0",
    description: "AegisRoute Google Colab LLM inference bridge & DevSecOps smart router",
    omnirouteApi: ">=1.0.0",
  },
  onRequest: async (ctx) => {
    return defaultInstance.onRequest(ctx);
  },
  onResponse: async (ctx, res) => {
    return defaultInstance.onResponse(ctx, res);
  },
  onError: async (ctx, err) => {
    return defaultInstance.onError(ctx, err);
  },
  onActivate: async () => {
    return defaultInstance.onInit();
  },
  onDeactivate: async () => {
    return defaultInstance.onDestroy();
  },
  onInstall: async () => {
    return defaultInstance.onInit();
  },
  register: (program, ctx) => {
    const aegisCmd = program
      .command("aegis")
      .description("Manage AegisRoute Colab GPU inference bridge & security routing");

    aegisCmd
      .command("status")
      .description("Show AegisRoute provider and tunnel status")
      .action(async (opts, cmd) => {
        console.log(JSON.stringify(defaultInstance.state, null, 2));
      });

    aegisCmd
      .command("tunnel <url>")
      .description("Update the active Cloudflare tunnel URL")
      .action(async (url) => {
        defaultInstance.updateTunnelUrl(url);
        console.log(`✓ Updated Aegis tunnel URL to: ${url}`);
      });

    aegisCmd
      .command("recover")
      .description("Trigger Playwright Colab headless reboot & recovery")
      .action(async () => {
        defaultInstance.triggerAutoRecovery();
        console.log("✓ Dispatched Colab auto-recovery");
      });
  }
};
