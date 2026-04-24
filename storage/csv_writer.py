import json
import logging
import pandas as pd
from decimal import Decimal

logger = logging.getLogger(__name__)

# TODO: Add save_data_to_db() here when moving off CSV.
# swap call in main.py from save_data_to_csv() to save_data_to_db()


def json_decimal_encoder(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def save_data_to_csv(data: list[dict], filename: str = "cvent_venue_details.csv"):
    """
    Saves a list of dictionaries (extracted venue data) to a CSV file.

    Args:
        data (list[dict]): A list of dictionaries, where each dictionary represents
                           a venue's details.
        filename (str): The name of the CSV file to save the data to.
    """
    if not data:
        logger.warning("No data to save.")
        return

    for i, venue in enumerate(data):
        logger.info(f"CSV payload [{i+1}/{len(data)}]:\n{json.dumps(venue, indent=2, default=json_decimal_encoder)}")

    try:
        df = pd.DataFrame(data)
        df.to_csv(filename, index=False, encoding='utf-8')
        logger.info(f"Data successfully saved to {filename}")
    except Exception as e:
        logger.exception(f"Error saving data to CSV: {e}")
