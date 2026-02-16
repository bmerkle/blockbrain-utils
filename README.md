# blockbrain-utils

Utility library for generating images via the [BlockBrain](https://theblockbrain.ai) platform.
Two implementations are provided — one using the **blockbrain_api SDK** and one using **pure REST/requests**.

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

## Configuration

The library reads its settings from environment variables (a `.env` file is loaded automatically).
You can also pass values explicitly via `BlockBrainConfig`:

| Variable | Description | Default |
|---|---|---|
| `BLOCKBRAIN_API_BASE` | Base URL of the BlockBrain API | `https://blocky.theblockbrain.ai` |
| `BLOCKBRAIN_API_TOKEN` | Bearer token for authentication | — |
| `BLOCKBRAIN_BOT_ID` | Bot / assistant ID | — |
| `BLOCKBRAIN_CHAT_MODEL` | Chat model identifier | `google-gemini-2.5-flash` |
| `BLOCKBRAIN_IMAGE_MODEL` | Image model identifier | `google-vertex-25-flash-image` |
| `BLOCKBRAIN_TENANT_DOMAIN` | Tenant domain | `sick` |

## Usage

### command line

```python

 python .\src\blockbrain_utils\bb_generate_image_API.py -i .\tests\data\example.jpg -o sample.png
```

### SDK-based client (recommended)

```python
from blockbrain_utils import BlockBrainConfig, generate_blockbrain_image

cfg = BlockBrainConfig(api_token="your-token", bot_id="your-bot-id")
result = generate_blockbrain_image(
    image_path="photo.jpg",
    prompt="Describe this image",
    cfg=cfg,
)
```

### REST-based client

```python
from blockbrain_utils.bb_generate_image_REST import (
    BlockBrainConfig,
    generate_blockbrain_image,
)

cfg = BlockBrainConfig(api_token="your-token", bot_id="your-bot-id")
result = generate_blockbrain_image(
    image_path="photo.jpg",
    prompt="Describe this image",
    cfg=cfg,
)
```

### Extracting the signed URL

```python
from blockbrain_utils import extract_signed_url

url = extract_signed_url(api_response_text)
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
│       ├── bb_generate_image_API.py   # SDK-based client
│       └── bb_generate_image_REST.py  # Pure REST client
├── tests/
│   ├── __init__.py
│   ├── test_bb_generate_image_API.py
│   └── data/
│       └── example.jpg
├── pyproject.toml
├── README.md
├── LICENSE
└── .gitignore
```

## License

MIT License — see LICENSE file for details.