# Contributing to Tranalyset

## Development Setup

1. **Clone the repository**
   ```bash
   git clone <repo-url>
   cd tranalyset
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   source .venv/bin/activate  # macOS/Linux
   ```

3. **Install dependencies**
   ```bash
   pip install -e ".[dev]"
   pip install fastapi uvicorn httpx
   ```

4. **Verify the setup**
   ```bash
   python -m pytest tests/unit/ -q
   ```

## Code Standards

- **Python version**: 3.12+ required
- **Type hints**: All public functions and methods must have type annotations
- **Imports**: Group standard library, third-party, and local imports; use `from __future__ import annotations`
- **Line length**: 100 characters max (enforced by Ruff)
- **Formatting**: Use Ruff for linting (`ruff check .`)
- **Type checking**: Run `mypy src/tranalyset/` before committing

## Testing

- **Location**: All tests go in `tests/unit/` or `tests/benchmarks/`
- **Framework**: pytest with `pytest-asyncio` (auto mode)
- **Coverage**: New features require corresponding tests
- **Running tests**:
  ```bash
  python -m pytest tests/unit/ -v          # Unit tests
  python -m pytest tests/benchmarks/ -v -s # Benchmarks
  python -m pytest tests/ -v               # All tests
  ```

## Architecture Guidelines

1. **Public interfaces**: Never change public interfaces (`__all__`, exported names, plugin contracts)
2. **Backward compatibility**: All changes must maintain backward compatibility
3. **Plugin isolation**: Plugins communicate only through events; never import another plugin's implementation
4. **Immutability**: Event envelopes must be frozen after creation
5. **Dependency injection**: Use the `ServiceContainer` for shared services; never use global state

## Pull Request Process

1. Ensure all tests pass
2. Add tests for new functionality
3. Update documentation if relevant
4. Run `ruff check .` and `mypy src/tranalyset/`
5. Submit PR with a clear description of changes

## Project Structure

```
src/tranalyset/
  api/            - FastAPI composition root
  contracts/      - Shared, stable interfaces
  kernel/         - Microkernel lifecycle and event bus
  plugins/        - Feature modules (trading, risk, signals, etc.)
  plugin_sdk/     - Base classes for plugin authors
  strategy_sdk/   - SDK for building trading strategies
tests/
  unit/           - Architecture-level unit tests
  benchmarks/     - Performance benchmarks
docs/             - Design documents and guides