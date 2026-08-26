# Contributing to AegisRoute

Thank you for your interest in contributing to **AegisRoute**! We welcome bug reports, feature requests, model adapters, and improvements to the OmniRoute routing layer.

---

## 🛠️ Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-org/AegisRoute.git
   cd AegisRoute
   ```

2. **Set up Python Virtual Environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   ```

3. **Install OmniRoute Plugin Dependencies:**
   ```bash
   cd plugin
   npm install
   cd ..
   ```

4. **Copy Environment Template:**
   ```bash
   cp config/aegis.env.example .env
   ```

---

## 🧪 Testing Guidelines

Before opening a Pull Request, make sure all test suites pass locally:

- **Run Tool-Calling Test:**
  ```bash
  chmod +x tests/test_tool_calling.sh
  ./tests/test_tool_calling.sh
  ```

- **Run Failover & Circuit-Breaker Simulation:**
  ```bash
  chmod +x tests/test_failover.sh
  ./tests/test_failover.sh
  ```

- **Verify CLI Help:**
  ```bash
  python3 cli/aegis.py --help
  ```

---

## 🌿 Branching & Pull Requests

1. Create a feature branch (`git checkout -b feat/your-feature-name` or `fix/issue-description`).
2. Adhere to [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat(core): add dynamic VRAM tiering`
   - `fix(plugin): fix cooldown timestamp comparison`
   - `docs(readme): add troubleshooting section for colab dialogs`
3. Ensure automated CI checks (Strix Security Audit, Linter) pass on your PR.

---

## 🔒 Security Vulnerabilities

If you discover a security vulnerability within AegisRoute itself, please do not file a public issue. Send details to security@aegisroute.dev or open a Private Security Advisory on GitHub.
