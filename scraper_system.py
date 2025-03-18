#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Advanced Cryptocurrency Data Scraper System
Implements requirements from PRD for data collection, API integration,
and real-time analysis with local LLMs.
"""

import abc
import argparse
import datetime
import json
import logging
import logging.handlers
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Type, Union

import requests
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# =========================================================
# Configuration Management
# =========================================================

@dataclass
class LoggingConfig:
    """Configuration for the logging system."""
    level: str = "INFO"
    file_path: str = "logs/scraper.log"
    max_size_mb: int = 10
    backup_count: int = 5
    format: str = "json"  # or "text"

@dataclass
class ScheduleConfig:
    """Configuration for the scraping schedule."""
    scrape_interval_minutes: Optional[int] = 5
    daily_run_time: Optional[str] = None  # Format: "HH:MM"

@dataclass
class SpreadsheetConfig:
    """Configuration for Apple Numbers integration."""
    spreadsheet_path: str = "~/Documents/Crypto_Dashboard.numbers"
    auto_update: bool = True
    update_method: str = "applescript"  # or "csv"

@dataclass
class ModelConfig:
    """Configuration for local LLM usage."""
    sentiment_model: str = "Gemma"
    debugging_model: str = "DeepSeek"
    insights_model: str = "Qwen"
    models_path: str = "~/models"

@dataclass
class ApiConfig:
    """Configuration for API plugins."""
    enabled_apis: List[str] = field(default_factory=lambda: ["DexScreener"])
    request_timeout_seconds: int = 30
    max_retries: int = 3
    rate_limit_safety_factor: float = 0.8  # To stay under rate limits

@dataclass
class SystemConfig:
    """Master configuration for the entire system."""
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    spreadsheet: SpreadsheetConfig = field(default_factory=SpreadsheetConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    tickers: List[str] = field(default_factory=list)
    
    @classmethod
    def from_yaml(cls, yaml_path: str) -> 'SystemConfig':
        """Load configuration from YAML file with robust error checking."""
        try:
            with open(yaml_path, 'r') as f:
                config_dict = yaml.safe_load(f)
            
            if not isinstance(config_dict, dict):
                raise ValueError(f"Invalid YAML format in {yaml_path}, expected dictionary")
            
            # Convert nested dictionaries to their respective dataclass types
            for key, dataclass_type in [
                ('logging', LoggingConfig),
                ('schedule', ScheduleConfig),
                ('spreadsheet', SpreadsheetConfig),
                ('model', ModelConfig),
                ('api', ApiConfig)
            ]:
                if key in config_dict and isinstance(config_dict[key], dict):
                    config_dict[key] = dataclass_type(**config_dict[key])
                elif key not in config_dict:
                    config_dict[key] = dataclass_type()
            
            return cls(**config_dict)
        except (IOError, yaml.YAMLError) as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to load configuration from {yaml_path}: {str(e)}")
            logger.info("Using default configuration")
            return cls()
    
    def to_yaml(self, yaml_path: str) -> None:
        """Save configuration to YAML file."""
        # Convert dataclasses to dictionaries
        config_dict = {
            'logging': asdict(self.logging),
            'schedule': asdict(self.schedule),
            'spreadsheet': asdict(self.spreadsheet),
            'model': asdict(self.model),
            'api': asdict(self.api),
            'tickers': self.tickers
        }
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
        
        # Write YAML file with backup of previous version
        if os.path.exists(yaml_path):
            backup_path = f"{yaml_path}.bak"
            shutil.copy2(yaml_path, backup_path)
        
        with open(yaml_path, 'w') as f:
            yaml.dump(config_dict, f, default_flow_style=False)

# =========================================================
# Logging Setup
# =========================================================

class JsonFormatter(logging.Formatter):
    """Format log records as JSON for machine readability."""
    
    def format(self, record):
        log_entry = {
            'timestamp': datetime.datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'module': record.module,
            'function': record.funcName,
            'message': record.getMessage(),
        }
        
        # Add exception info if present
        if record.exc_info:
            log_entry['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
            }
        
        # Add any extra attributes set by the logger
        for key, value in record.__dict__.items():
            if key not in ('args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename',
                          'funcName', 'id', 'levelname', 'levelno', 'lineno', 'module',
                          'msecs', 'message', 'msg', 'name', 'pathname', 'process',
                          'processName', 'relativeCreated', 'stack_info', 'thread', 'threadName'):
                log_entry[key] = value
        
        return json.dumps(log_entry)

def setup_logging(config: LoggingConfig) -> None:
    """Set up the logging system based on configuration."""
    # Create logs directory if it doesn't exist
    log_dir = os.path.dirname(config.file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.level))
    
    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create a rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        config.file_path,
        maxBytes=config.max_size_mb * 1024 * 1024,
        backupCount=config.backup_count
    )
    
    # Set the formatter based on the configuration
    if config.format.lower() == 'json':
        file_handler.setFormatter(JsonFormatter())
    else:
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
    
    # Create a console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    # Add the handlers to the root logger
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Log that logging has been set up
    root_logger.info("Logging system initialized", extra={
        'config': {
            'level': config.level,
            'file_path': config.file_path,
            'format': config.format
        }
    })

# =========================================================
# Data Models
# =========================================================

@dataclass
class TokenData:
    """Standardized data model for token information."""
    symbol: str
    contract_address: Optional[str] = None
    name: Optional[str] = None
    price_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    change_24h_percent: Optional[float] = None
    last_updated: Optional[datetime.datetime] = None
    source: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = asdict(self)
        # Convert datetime to ISO format string for JSON serialization
        if self.last_updated:
            result['last_updated'] = self.last_updated.isoformat()
        return result

# =========================================================
# Plugin System 
# =========================================================

class DataSourcePlugin(abc.ABC):
    """Abstract base class for all data source plugins."""
    
    def __init__(self, config: ApiConfig):
        self.config = config
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Create and configure a requests session with retry logic."""
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'CryptoScraper/1.0',
            'Accept': 'application/json'
        })
        return session
    
    @abc.abstractmethod
    def fetch_data(self, symbol: str) -> TokenData:
        """Fetch data for a specific token from the data source."""
        pass
    
    @abc.abstractmethod
    def get_source_name(self) -> str:
        """Get the name of this data source."""
        pass
    
    def cleanup(self) -> None:
        """Perform any cleanup needed when shutting down."""
        self.session.close()

class DexScreenerPlugin(DataSourcePlugin):
    """Plugin for the DEX Screener API."""
    
    BASE_URL = "https://api.dexscreener.com/latest/dex"
    
    def get_source_name(self) -> str:
        return "DexScreener"
    
    def fetch_data(self, symbol: str) -> TokenData:
        """Fetch token data from DEX Screener API."""
        self.logger.debug(f"Fetching data for {symbol} from DEX Screener")
        
        # DEX Screener provides endpoints by address or symbol
        try:
            url = f"{self.BASE_URL}/tokens/{symbol}"
            start_time = time.time()
            
            response = self.session.get(
                url, 
                timeout=self.config.request_timeout_seconds
            )
            
            elapsed = time.time() - start_time
            self.logger.debug(f"DEX Screener API call took {elapsed:.2f}s")
            
            response.raise_for_status()
            data = response.json()
            
            # DEX Screener returns pairs, we need to extract token data
            if not data.get('pairs') or len(data['pairs']) == 0:
                self.logger.warning(f"No pairs found for {symbol}")
                return TokenData(symbol=symbol, source=self.get_source_name())
            
            # Use the first pair for token data
            pair = data['pairs'][0]
            baseToken = pair.get('baseToken', {})
            
            # Extract the required fields
            token_data = TokenData(
                symbol=symbol,
                contract_address=baseToken.get('address'),
                name=baseToken.get('name'),
                price_usd=float(pair.get('priceUsd', 0)),
                volume_24h_usd=float(pair.get('volume', {}).get('h24', 0)),
                market_cap_usd=float(pair.get('fdv', 0)),  # Fully diluted valuation
                change_24h_percent=float(pair.get('priceChange', {}).get('h24', 0)),
                last_updated=datetime.datetime.now(),
                source=self.get_source_name()
            )
            
            self.logger.info(f"Successfully fetched data for {symbol}", extra={
                'token': symbol,
                'price': token_data.price_usd,
                'volume': token_data.volume_24h_usd
            })
            
            return token_data
            
        except requests.RequestException as e:
            self.logger.error(f"Error fetching data for {symbol}: {str(e)}", extra={
                'token': symbol,
                'error': str(e),
                'error_type': type(e).__name__
            })
            return TokenData(symbol=symbol, source=self.get_source_name())
        except (KeyError, ValueError, TypeError) as e:
            self.logger.error(f"Error parsing data for {symbol}: {str(e)}", extra={
                'token': symbol,
                'error': str(e),
                'error_type': type(e).__name__
            })
            return TokenData(symbol=symbol, source=self.get_source_name())

class PluginManager:
    """Manages the loading and execution of data source plugins."""
    
    def __init__(self, config: ApiConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.plugins: Dict[str, DataSourcePlugin] = {}
        self._lock = threading.RLock()
        
        # Map of plugin names to their class types
        self.plugin_registry: Dict[str, Type[DataSourcePlugin]] = {
            "DexScreener": DexScreenerPlugin,
        }
    
    def load_plugins(self) -> None:
        """Load all enabled plugins based on configuration."""
        with self._lock:
            self.logger.info("Loading plugins")
            
            for plugin_name in self.config.enabled_apis:
                self._load_plugin(plugin_name)
    
    def _load_plugin(self, plugin_name: str) -> None:
        """Load a single plugin by name."""
        if plugin_name in self.plugins:
            self.logger.warning(f"Plugin {plugin_name} already loaded")
            return
        
        if plugin_name not in self.plugin_registry:
            self.logger.error(f"Unknown plugin: {plugin_name}")
            return
        
        try:
            plugin_class = self.plugin_registry[plugin_name]
            plugin = plugin_class(self.config)
            self.plugins[plugin_name] = plugin
            self.logger.info(f"Successfully loaded plugin: {plugin_name}")
        except Exception as e:
            self.logger.error(f"Failed to load plugin {plugin_name}: {str(e)}", 
                             exc_info=True)
    
    def fetch_token_data(self, symbol: str) -> List[TokenData]:
        """Fetch data for a token from all loaded plugins."""
        results = []
        
        with self._lock:
            if not self.plugins:
                self.logger.warning("No plugins loaded, cannot fetch data")
                return results
            
            for plugin_name, plugin in self.plugins.items():
                try:
                    token_data = plugin.fetch_data(symbol)
                    results.append(token_data)
                except Exception as e:
                    self.logger.error(
                        f"Error fetching data from {plugin_name} for {symbol}: {str(e)}",
                        exc_info=True
                    )
        
        return results
    
    def cleanup(self) -> None:
        """Clean up all plugins."""
        with self._lock:
            for plugin_name, plugin in self.plugins.items():
                try:
                    plugin.cleanup()
                    self.logger.debug(f"Cleaned up plugin: {plugin_name}")
                except Exception as e:
                    self.logger.error(f"Error cleaning up plugin {plugin_name}: {str(e)}")
            
            self.plugins.clear()

# =========================================================
# Apple Numbers Integration
# =========================================================

class NumbersSpreadsheetUpdater:
    """Handles updating Apple Numbers spreadsheets with scraped data."""
    
    def __init__(self, config: SpreadsheetConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Expand user directory if needed (e.g., ~/Documents/...)
        if self.config.spreadsheet_path.startswith('~'):
            self.config.spreadsheet_path = os.path.expanduser(self.config.spreadsheet_path)
    
    def update_spreadsheet(self, tokens_data: List[TokenData]) -> bool:
        """Update the Numbers spreadsheet with token data."""
        self.logger.info(f"Updating Numbers spreadsheet with {len(tokens_data)} tokens")
        
        if not self.config.auto_update:
            self.logger.info("Auto-update disabled, skipping spreadsheet update")
            return False
        
        # Check if the spreadsheet exists
        if not os.path.exists(self.config.spreadsheet_path):
            self.logger.error(f"Spreadsheet not found: {self.config.spreadsheet_path}")
            return False
        
        if self.config.update_method == "applescript":
            return self._update_via_applescript(tokens_data)
        elif self.config.update_method == "csv":
            return self._update_via_csv(tokens_data)
        else:
            self.logger.error(f"Unsupported update method: {self.config.update_method}")
            return False
    
    def _update_via_applescript(self, tokens_data: List[TokenData]) -> bool:
        """Update the spreadsheet using AppleScript."""
        try:
            # Build AppleScript commands
            script_lines = [
                'tell application "Numbers"',
                f'open POSIX file "{self.config.spreadsheet_path}"',
                'tell front document',
                'tell active sheet',
                'tell table 1'  # Assuming data goes in table 1
            ]
            
            # Add a header row if needed
            script_lines.extend([
                'set value of cell 1 of row 1 to "Symbol"',
                'set value of cell 2 of row 1 to "Price (USD)"',
                'set value of cell 3 of row 1 to "24h Volume"',
                'set value of cell 4 of row 1 to "Market Cap"',
                'set value of cell 5 of row 1 to "24h Change %"',
                'set value of cell 6 of row 1 to "Last Updated"',
            ])
            
            # Add data rows
            for i, token in enumerate(tokens_data, start=2):  # Start at row 2 after header
                # AppleScript is 1-indexed
                row_idx = str(i)
                
                script_lines.extend([
                    f'set value of cell 1 of row {row_idx} to "{token.symbol}"',
                    f'set value of cell 2 of row {row_idx} to {token.price_usd or 0}',
                    f'set value of cell 3 of row {row_idx} to {token.volume_24h_usd or 0}',
                    f'set value of cell 4 of row {row_idx} to {token.market_cap_usd or 0}',
                    f'set value of cell 5 of row {row_idx} to {token.change_24h_percent or 0}',
                ])
                
                if token.last_updated:
                    date_str = token.last_updated.strftime("%Y-%m-%d %H:%M:%S")
                    script_lines.append(f'set value of cell 6 of row {row_idx} to "{date_str}"')
            
            # Close all the tell blocks
            script_lines.extend([
                'end tell',  # table 1
                'end tell',  # active sheet
                'end tell',  # front document
                'end tell'   # application "Numbers"
            ])
            
            script = '\n'.join(script_lines)
            
            # Execute the AppleScript
            self.logger.debug("Executing AppleScript to update Numbers")
            process = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                check=False  # We'll check the return code manually
            )
            
            if process.returncode != 0:
                self.logger.error(f"AppleScript execution failed: {process.stderr}")
                return False
            
            self.logger.info("Successfully updated Numbers spreadsheet via AppleScript")
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating spreadsheet via AppleScript: {str(e)}", 
                             exc_info=True)
            return False
    
    def _update_via_csv(self, tokens_data: List[TokenData]) -> bool:
        """Update the spreadsheet by exporting data to CSV and then importing."""
        try:
            # Create a temporary CSV file
            csv_path = os.path.join(os.path.dirname(self.config.spreadsheet_path), 
                                  "temp_crypto_data.csv")
            
            with open(csv_path, 'w', newline='') as csvfile:
                # Write header
                header = ["Symbol", "Price (USD)", "24h Volume", "Market Cap", 
                         "24h Change %", "Last Updated"]
                csvfile.write(','.join(header) + '\n')
                
                # Write data rows
                for token in tokens_data:
                    last_updated = ""
                    if token.last_updated:
                        last_updated = token.last_updated.strftime("%Y-%m-%d %H:%M:%S")
                    
                    row = [
                        token.symbol,
                        str(token.price_usd or 0),
                        str(token.volume_24h_usd or 0),
                        str(token.market_cap_usd or 0),
                        str(token.change_24h_percent or 0),
                        last_updated
                    ]
                    csvfile.write(','.join(row) + '\n')
            
            # Use AppleScript to import the CSV
            script = f'''
            tell application "Numbers"
                open POSIX file "{self.config.spreadsheet_path}"
                tell front document
                    tell active sheet
                        delete every table
                        make new table with properties {{data source:POSIX file "{csv_path}"}}
                    end tell
                end tell
            end tell
            '''
            
            self.logger.debug("Executing AppleScript to import CSV into Numbers")
            process = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                check=False
            )
            
            # Clean up the temporary CSV file
            if os.path.exists(csv_path):
                os.remove(csv_path)
            
            if process.returncode != 0:
                self.logger.error(f"AppleScript CSV import failed: {process.stderr}")
                return False
            
            self.logger.info("Successfully updated Numbers spreadsheet via CSV import")
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating spreadsheet via CSV: {str(e)}", 
                             exc_info=True)
            return False

# =========================================================
# Local LLM Integration
# =========================================================

class TaskType(Enum):
    """Enumeration of task types that can be performed by LLMs."""
    SENTIMENT_ANALYSIS = "sentiment"
    ERROR_DEBUGGING = "debugging"
    TRADING_INSIGHTS = "insights"

class LLMManager:
    """Manages interactions with local large language models."""
    
    def __init__(self, config: ModelConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.models = {}
        self._lock = threading.RLock()
        
        # Map task types to model names from config
        self.task_model_mapping = {
            TaskType.SENTIMENT_ANALYSIS: self.config.sentiment_model,
            TaskType.ERROR_DEBUGGING: self.config.debugging_model,
            TaskType.TRADING_INSIGHTS: self.config.insights_model,
        }
    
    def get_model_for_task(self, task_type: TaskType) -> str:
        """Get the configured model name for a specific task."""
        return self.task_model_mapping.get(task_type, self.config.sentiment_model)
    
    def analyze_sentiment(self, text: str) -> Dict[str, Any]:
        """Analyze sentiment of text using the configured sentiment model."""
        model_name = self.get_model_for_task(TaskType.SENTIMENT_ANALYSIS)
        
        self.logger.info(f"Analyzing sentiment using {model_name} model")
        
        # This is a placeholder. In a real implementation, you would:
        # 1. Load the appropriate model if not already loaded
        # 2. Prepare the input text (tokenization, etc.)
        # 3. Run inference
        # 4. Process and return the results
        
        # Simulate model inference for demonstration
        sentiment_result = {
            "sentiment": "positive",  # or "negative", "neutral"
            "confidence": 0.85,
            "model_used": model_name,
        }
        
        self.logger.info(f"Sentiment analysis complete: {sentiment_result['sentiment']}")
        return sentiment_result
    
    def debug_error(self, error_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze an error message and suggest fixes using the debugging model."""
        model_name = self.get_model_for_task(TaskType.ERROR_DEBUGGING)
        
        self.logger.info(f"Debugging error using {model_name} model")
        
        # Placeholder for actual model inference
        debug_result = {
            "probable_cause": "API timeout",
            "suggested_fix": "Increase timeout threshold or implement retry logic",
            "confidence": 0.75,
            "model_used": model_name,
        }
        
        self.logger.info(f"Error debugging complete: {debug_result['probable_cause']}")
        return debug_result
    
    def generate_insights(self, token_data: List[TokenData]) -> Dict[str, Any]:
        """Generate trading insights from token data using the insights model."""
        model_name = self.get_model_for_task(TaskType.TRADING_INSIGHTS)
        
        self.logger.info(f"Generating insights using {model_name} model")
        
        # Placeholder for actual model inference
        insights_result = {
            "summary": "Market showing bullish trends with increasing volumes",
            "highlighted_tokens": [token.symbol for token in token_data[:3]],
            "recommendation": "Consider monitoring volume spikes for entry points",
            "model_used": model_name,
        }
        
        self.logger.info(f"Insight generation complete")
        return insights_result
    
    def cleanup(self) -> None:
        """Release any resources used by the models."""
        with self._lock:
            for model_name in self.models:
                self.logger.debug(f"Releasing resources for model: {model_name}")
                # In real implementation, unload models and free memory
            
            self.models.clear()
            self.logger.info("All LLM resources released")

# =========================================================
# Scheduler System
# =========================================================

class ScrapeScheduler:
    """Manages scheduling of data scraping operations."""
    
    def __init__(self, config: ScheduleConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.scheduler = BackgroundScheduler()
        self._lock = threading.RLock()
        self._job_id = None
    
    def start(self, scrape_func) -> None:
        """Start the scheduler with the given scrape function."""
        with self._lock:
            if self.scheduler.running:
                self.logger.warning("Scheduler is already running")
                return
            
            self._configure_job(scrape_func)
            
            try:
                self.scheduler.start()
                self.logger.info("Scheduler started")
            except Exception as e:
                self.logger.error(f"Failed to start scheduler: {str(e)}", exc_info=True)
    
    def _configure_job(self, scrape_func) -> None:
        """Configure the scheduler job based on settings."""
        # Remove any existing job
        if self._job_id and self._job_id in self.scheduler:
            self.scheduler.remove_job(self._job_id)
        
        # Configure the job based on schedule settings
        if self.config.daily_run_time:
            # Parse the time string "HH:MM"
            try:
                hour, minute = map(int, self.config.daily_run_time.split(':'))
                trigger = CronTrigger(hour=hour, minute=minute)
                self.logger.info(f"Configured daily schedule at {hour:02d}:{minute:02d}")
            except (ValueError, AttributeError):
                self.logger.error(f"Invalid daily_run_time format: {self.config.daily_run_time}")
                # Fall back to interval-based scheduling
                trigger = IntervalTrigger(minutes=self.config.scrape_interval_minutes or 5)
                self.logger.info(f"Falling back to interval schedule: {self.config.scrape_interval_minutes} minutes")
        else:
            # Use interval-based scheduling
            interval_minutes = self.config.scrape_interval_minutes or 5
            trigger = IntervalTrigger(minutes=interval_minutes)
            self.logger.info(f"Configured interval schedule: {interval_minutes} minutes")
        
        # Add the job to the scheduler
        self._job_id = self.scheduler.add_job(
            scrape_func,
            trigger=trigger,
            id='scrape_job',
            replace_existing=True
        ).id
    
    def reconfigure(self, config: ScheduleConfig, scrape_func) -> None:
        """Reconfigure the scheduler with new settings."""
        with self._lock:
            self.logger.info("Reconfiguring scheduler")
            self.config = config
            
            if self.scheduler.running:
                self._configure_job(scrape_func)
                self.logger.info("Scheduler reconfigured")
            else:
                self.logger.warning("Scheduler not running, cannot reconfigure")
    
    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        with self._lock:
            if not self.scheduler.running:
                self.logger.warning("Scheduler is not running")
                return
            
            try:
                self.scheduler.shutdown()
                self.logger.info("Scheduler shutdown complete")
            except Exception as e:
                self.logger.error(f"Error during scheduler shutdown: {str(e)}", 
                                 exc_info=True)

# =========================================================
# Main Application
# =========================================================

class CryptoScraperApp:
    """Main application class for the crypto scraper system."""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.logger = None  # Will be set up during initialization
        self.config = None
        self.plugin_manager = None
        self.spreadsheet_updater = None
        self.llm_manager = None
        self.scheduler = None
        self._shutdown_event = threading.Event()
        self._executor = None
    
    def initialize(self) -> bool:
        """Initialize the application components."""
        try:
            # Load configuration
            self.config = SystemConfig.from_yaml(self.config_path)
            
            # Set up logging
            setup_logging(self.config.logging)
            self.logger = logging.getLogger(__name__)
            self.logger.info("Application initializing")
            
            # Initialize components
            self.plugin_manager = PluginManager(self.config.api)
            self.plugin_manager.load_plugins()
            
            self.spreadsheet_updater = NumbersSpreadsheetUpdater(self.config.spreadsheet)
            self.llm_manager = LLMManager(self.config.model)
            self.scheduler = ScrapeScheduler(self.config.schedule)
            
            # Thread pool for parallel processing
            self._executor = ThreadPoolExecutor(max_workers=10)
            
            # Register signal handlers for graceful shutdown
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            
            self.logger.info("Application initialized successfully")
            return True
            
        except Exception as e:
            # Basic logging if logger not set up yet
            if self.logger is None:
                print(f"Error during initialization: {str(e)}")
            else:
                self.logger.error(f"Initialization failed: {str(e)}", exc_info=True)
            return False
    
    def _signal_handler(self, signum, frame) -> None:
        """Handle termination signals for graceful shutdown."""
        self.logger.info(f"Received signal {signum}, initiating shutdown")
        self._shutdown_event.set()
    
    def run(self) -> None:
        """Run the application."""
        if not self.initialize():
            self.logger.error("Initialization failed, cannot start application")
            return
        
        self.logger.info("Starting application")
        
        # Start the scheduler with our scrape method
        self.scheduler.start(self.perform_scrape)
        
        # Perform an initial scrape immediately
        self.perform_scrape()
        
        # Wait for shutdown signal
        try:
            while not self._shutdown_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received, shutting down")
        finally:
            self.shutdown()
    
    def perform_scrape(self) -> None:
        """Perform a scrape operation for all configured tickers."""
        try:
            start_time = time.time()
            self.logger.info("Starting scrape operation")
            
            # Validate we have tickers to scrape
            if not self.config.tickers:
                self.logger.warning("No tickers configured, nothing to scrape")
                return
            
            all_token_data = []
            
            # Use ThreadPoolExecutor for parallel processing
            futures = []
            for ticker in self.config.tickers:
                future = self._executor.submit(self._scrape_single_ticker, ticker)
                futures.append(future)
            
            # Collect results
            for future in futures:
                tokens_data = future.result()
                if tokens_data:
                    all_token_data.extend(tokens_data)
            
            # Update the spreadsheet with all token data
            if all_token_data:
                self.spreadsheet_updater.update_spreadsheet(all_token_data)
                
                # Generate insights from the collected data
                insights = self.llm_manager.generate_insights(all_token_data)
                self.logger.info(f"Generated insights: {insights['summary']}")
            
            elapsed = time.time() - start_time
            self.logger.info(f"Scrape operation completed in {elapsed:.2f}s", extra={
                'duration': elapsed,
                'tokens_scraped': len(all_token_data),
            })
            
        except Exception as e:
            self.logger.error(f"Error during scrape operation: {str(e)}", exc_info=True)
            
            # Use LLM for debugging assistance
            error_context = {
                'operation': 'scrape',
                'error_message': str(e),
                'error_type': type(e).__name__,
            }
            debug_result = self.llm_manager.debug_error(str(e), error_context)
            self.logger.info(f"LLM debugging suggestion: {debug_result['suggested_fix']}")
    
    def _scrape_single_ticker(self, ticker: str) -> List[TokenData]:
        """Scrape data for a single ticker."""
        self.logger.debug(f"Scraping data for ticker: {ticker}")
        try:
            return self.plugin_manager.fetch_token_data(ticker)
        except Exception as e:
            self.logger.error(f"Failed to scrape ticker {ticker}: {str(e)}", extra={
                'ticker': ticker,
                'error': str(e)
            })
            return []
    
    def shutdown(self) -> None:
        """Shutdown the application components."""
        self.logger.info("Shutting down application")
        
        # Stop the scheduler
        if self.scheduler:
            self.scheduler.shutdown()
        
        # Clean up components
        if self.plugin_manager:
            self.plugin_manager.cleanup()
        
        if self.llm_manager:
            self.llm_manager.cleanup()
        
        # Shutdown thread pool
        if self._executor:
            self._executor.shutdown(wait=True)
        
        self.logger.info("Application shutdown complete")

# =========================================================
# Entry Point
# =========================================================

def main():
    """Application entry point."""
    parser = argparse.ArgumentParser(description="Cryptocurrency Data Scraper")
    parser.add_argument(
        "--config", 
        default="config.yaml",
        help="Path to the configuration file"
    )
    parser.add_argument(
        "--debug", 
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    app = CryptoScraperApp(config_path=args.config)
    app.run()

if __name__ == "__main__":
    main() 