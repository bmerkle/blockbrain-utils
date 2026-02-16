# blockbrain-utils

A collection of utility functions and tools for Python projects.

## Installation

### From source

```bash
git clone https://github.com/bmerkle/blockbrain-utils.git
cd blockbrain-utils
pip install -e .
```

### For development

```bash
pip install -e ".[dev]"
```

## Usage

```python
from blockbrain_utils.utils import greet, add

# Use the greet function
message = greet("World")
print(message)  # Output: Hello, World!

# Use the add function
result = add(2, 3)
print(result)  # Output: 5
```

## Development

### Running tests

```bash
pytest
```

### Running tests with coverage

```bash
pytest --cov=blockbrain_utils --cov-report=html
```

## Project Structure

```
blockbrain-utils/
├── src/
│   └── blockbrain_utils/
│       ├── __init__.py
│       └── utils.py
├── tests/
│   ├── __init__.py
│   └── test_utils.py
├── pyproject.toml
├── README.md
├── LICENSE
└── .gitignore
```

## License

MIT License - see LICENSE file for details.