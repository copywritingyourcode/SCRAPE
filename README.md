# Cryptocurrency Data Scraper System

A robust, configurable system for scraping cryptocurrency data from multiple sources, analyzing it using local LLMs, and updating an Apple Numbers spreadsheet in real-time.

## Features

- **Automated Updates to Numbers Spreadsheet**: Real-time sync of scraped data to a Numbers spreadsheet
- **DEX Screener API Integration**: Fetch key details for cryptocurrency tokens
- **Configurable Scrape Scheduling**: Flexible time interval or specific daily time scheduling
- **Modular API Plugin System**: Easily add new data sources without modifying core code
- **Advanced Logging**: Structured JSON logging with file rotation for debugging and analytics
- **Local LLM Integration**: Use different models for sentiment analysis, debugging assistance, and trading insights

## Requirements

- Python 3.8+
- macOS (for Apple Numbers integration)
- Local LLM models (Gemma, DeepSeek, Qwen) if using the LLM features
- Internet connection for API access

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/crypto-scraper.git
cd crypto-scraper
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install required packages:
```bash
pip install requests pyyaml apscheduler
```

4. Create a configuration file (or use the provided template):
```bash
cp config.yaml.example config.yaml
```

5. Edit the config.yaml file to set your preferences and add your cryptocurrency tickers.

## Usage

### Basic Usage

```bash
python scraper_system.py
```

### With Custom Configuration

```bash
python scraper_system.py --config my_custom_config.yaml
```

### Enable Debug Logging

```bash
python scraper_system.py --debug
```

## Configuration

All aspects of the system are configurable through the `config.yaml` file:

### Logging Configuration

```yaml
logging:
  level: "INFO"                     # Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
  file_path: "logs/scraper.log"     # Path to write log files
  max_size_mb: 10                   # Maximum size of each log file in MB
  backup_count: 5                   # Number of backup log files to keep
  format: "json"                    # Log format: json or text
```

### Schedule Configuration

```yaml
schedule:
  scrape_interval_minutes: 5        # Interval between scrapes in minutes
  daily_run_time: "08:00"           # Optional specific daily time (HH:MM format)
```

Setting `daily_run_time` overrides the interval-based scheduling. Leave it as `null` to use interval-based scraping.

### Apple Numbers Integration

```yaml
spreadsheet:
  spreadsheet_path: "~/Documents/Crypto_Dashboard.numbers"  # Path to Numbers spreadsheet
  auto_update: true                 # Whether to automatically update the spreadsheet
  update_method: "applescript"      # Update method: applescript or csv
```

### API Configuration

```yaml
api:
  enabled_apis:                     # List of enabled API plugins
    - "DexScreener"
  request_timeout_seconds: 30       # Timeout for API requests
  max_retries: 3                    # Maximum number of retries for failed requests
  rate_limit_safety_factor: 0.8     # Factor to stay under API rate limits (0.0-1.0)
```

### Local LLM Configuration

```yaml
model:
  sentiment_model: "Gemma"          # Model for sentiment analysis
  debugging_model: "DeepSeek"       # Model for error debugging
  insights_model: "Qwen"            # Model for trading insights
  models_path: "~/models"           # Path to local model files
```

### Tracked Tickers

```yaml
tickers:
  - "ETH"
  - "BTC"
  - "SOL"
  - "AVAX"
  - "MATIC"
```

## Developing New API Plugins

The system is designed for easy extensibility. To add a new API data source:

1. Create a new class that inherits from `DataSourcePlugin` in a new file (e.g., `coingecko_plugin.py`):

```python
from scraper_system import DataSourcePlugin, TokenData

class CoinGeckoPlugin(DataSourcePlugin):
    """Plugin for the CoinGecko API."""
    
    BASE_URL = "https://api.coingecko.com/api/v3"
    
    def get_source_name(self) -> str:
        return "CoinGecko"
    
    def fetch_data(self, symbol: str) -> TokenData:
        """Fetch token data from CoinGecko API."""
        self.logger.debug(f"Fetching data for {symbol} from CoinGecko")
        
        try:
            # Make API call to CoinGecko
            url = f"{self.BASE_URL}/coins/{symbol.lower()}"
            response = self.session.get(url, timeout=self.config.request_timeout_seconds)
            response.raise_for_status()
            data = response.json()
            
            # Extract relevant data
            token_data = TokenData(
                symbol=symbol,
                name=data.get('name'),
                price_usd=data.get('market_data', {}).get('current_price', {}).get('usd'),
                volume_24h_usd=data.get('market_data', {}).get('total_volume', {}).get('usd'),
                market_cap_usd=data.get('market_data', {}).get('market_cap', {}).get('usd'),
                change_24h_percent=data.get('market_data', {}).get('price_change_percentage_24h'),
                last_updated=datetime.datetime.now(),
                source=self.get_source_name()
            )
            
            return token_data
            
        except Exception as e:
            self.logger.error(f"Error fetching data from CoinGecko: {str(e)}")
            # Return a minimal token data object
            return TokenData(symbol=symbol, source=self.get_source_name())
```

2. Register your plugin in the `PluginManager`'s plugin registry:

```python
# Add to the plugin_registry dictionary in PluginManager.__init__
self.plugin_registry: Dict[str, Type[DataSourcePlugin]] = {
    "DexScreener": DexScreenerPlugin,
    "CoinGecko": CoinGeckoPlugin,  # Add your new plugin here
}
```

3. Enable your plugin in the configuration file:

```yaml
api:
  enabled_apis:
    - "DexScreener"
    - "CoinGecko"  # Add your new plugin here
```

## Troubleshooting

### Logs

Check the log file (default: `logs/scraper.log`) for error messages and debugging information.

### Common Issues

- **Numbers Spreadsheet Not Found**: Ensure the path in the configuration file is correct.
- **API Rate Limiting**: If you encounter rate limit errors, increase the scrape interval or adjust the `rate_limit_safety_factor`.
- **AppleScript Errors**: Ensure Numbers is installed and can be opened by the user running the script.

## License

MIT License

## Acknowledgments

- DEX Screener for providing the API
- The Python community for excellent libraries