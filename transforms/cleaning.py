import re
from decimal import Decimal, InvalidOperation


def sanitize_data_types(data: dict):
    """
    Sanitizes and converts data types for specified fields in the venue data dictionary.
    """
    # Fields to convert to integer
    fields_to_int = [
        "total_guest_rooms",
        "single_1_bed_rooms",
        "double_2_bed_rooms",
        "suite_rooms",
        "year_built",
        "year_renovated",
        "meeting_room_count",
        "max_capacity",
        "aaa_ratings",
        "travelstar_rating",
        "forbes_rating"
    ]

    # Convert known fields to integers
    for field in fields_to_int:
        if field in data and isinstance(data[field], str) and data[field] is not None:
            try:
                # Remove any non-digit characters (like commas)
                cleaned_value = re.sub(r'[^0-9]', '', data[field])
                if cleaned_value:  # Ensure it's not an empty string after cleaning
                    data[field] = int(cleaned_value)
                else:
                    data[field] = None
            except ValueError:
                data[field] = None  # Set to None if conversion fails
        elif field in data and not isinstance(data[field], (int, float, type(None), Decimal)):
            data[field] = None

    # Specific handling for fields that contain units (e.g., 'sq. ft.') or percentage signs and should be float/decimal
    fields_with_units_to_decimal = []
    for field in fields_with_units_to_decimal:
        if field in data and isinstance(data[field], str) and data[field] is not None:
            try:
                # Remove units like 'sq. ft.', 'sqm', 'm^2', '%' and commas
                # Then extract numeric part
                cleaned_value = re.sub(r'[a-zA-Z%\s,]', '', data[field])
                if cleaned_value:
                    data[field] = Decimal(cleaned_value)
                else:
                    data[field] = None
            except InvalidOperation:
                data[field] = None
        elif field in data and not isinstance(data[field], (int, float, type(None), Decimal)):
            data[field] = None


def format_airport_distances(distance_texts):
    """
    Formats airport distance strings into a dictionary:
    {
        "Airport Name (CODE)": "13mi"
    }
    """

    formatted = {}

    for text in distance_texts:
        if not text:
            continue

        text = text.strip()

        # Match distance (number + unit)
        distance_match = re.search(r"(\d+(?:\.\d+)?)\s*(mi|miles|km)", text, re.I)
        airport_match = re.search(r"from\s+(.*)", text, re.I)

        if not distance_match:
            continue

        distance_value = (
            f"{distance_match.group(1)}"
            f"{distance_match.group(2).lower().replace('miles', 'mi')}"
        )

        airport_name = (
            airport_match.group(1).strip()
            if airport_match
            else "Airport Name Not Found"
        )

        formatted[airport_name] = distance_value

    return formatted


def rename_property_fields(payload: dict) -> dict:
    """
    Mutates the input payload by renaming specific fields.
    Returns the same payload reference for convenience.
    """

    field_mapping = {
        "single_1_beds": "single_1_bed_rooms",
        "double_2_beds": "double_2_beds_rooms",
        "suites": "suite_rooms",
        "meeting_rooms": "meeting_rooms_count",
        "largest_room": "largest_meeting_room",
        "second_largest_room": "second_largest_meeting_room",
    }

    for old_key, new_key in field_mapping.items():
        if old_key in payload:
            payload[new_key] = payload.pop(old_key)

    return payload


def validate_schema(payload: dict, schema_cls) -> dict:
    """
    Mutates the payload by removing fields not present in the schema.
    Returns the same payload reference.
    """

    allowed_fields = set(schema_cls.model_fields.keys())

    for key in list(payload.keys()):
        if key not in allowed_fields:
            payload.pop(key)

    return payload
