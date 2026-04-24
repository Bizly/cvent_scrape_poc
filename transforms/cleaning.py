import re
from decimal import Decimal, InvalidOperation
from typing import Dict, Any


def sanitize_data_types(data: Any):
    """
    Sanitizes and converts data types for specified fields in the venue data dictionary.
    """
    # Fields to convert to integer
    fields_to_int = [
        "total_guest_rooms",
        "single_1_bed_rooms",
        "double_2_beds_rooms",
        "suite_rooms",
        "year_built",
        "year_renovated",
        "meeting_room_count",
        "max_capacity",
        "aaa_rating",
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


def rename_property_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mutates the input payload by renaming specific fields.
    Returns the same payload reference for convenience.
    """

    # Cvent guest/meeting metrics use human labels slugified to keys; variants differ by property.
    field_mapping = {
        "single_1_beds": "single_1_bed_rooms",
        "singles_1_bed": "single_1_bed_rooms",
        "single_1_bed": "single_1_bed_rooms",
        "double_2_beds": "double_2_beds_rooms",
        "doubles_2_beds": "double_2_beds_rooms",
        "doubles_2_bed": "double_2_beds_rooms",
        "double_2_bed": "double_2_beds_rooms",
        "suites": "suite_rooms",
        "suite": "suite_rooms",
        "meeting_rooms": "meeting_room_count",
        "total_meeting_rooms": "meeting_room_count",
        "number_of_meeting_rooms": "meeting_room_count",
        "largest_room": "largest_meeting_room",
        "second_largest_room": "second_largest_meeting_room",
        "number_of_guest_rooms": "total_guest_rooms",
        "guest_rooms": "total_guest_rooms",
    }

    for old_key, new_key in field_mapping.items():
        if old_key in payload:
            payload[new_key] = payload.pop(old_key)

    return payload


def _nonempty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())


def ensure_required_venue_schema_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    After rename_property_fields, fill VenueDetailsSchema-required keys that Cvent
    sometimes omits or exposes under different metric labels. Values are strings
    where sanitize_data_types expects numeric strings for int fields.
    """
    # --- square_footage (str): prefer totals, then any sq-ft style line ---
    sq = payload.get("square_footage")
    if not _nonempty_str(sq):
        for k in (
            "total_square_footage",
            "total_meeting_space",
            "total_event_space",
            "meeting_space_sq_ft",
            "meeting_space",
            "net_assignable_square_feet",
            "gross_meeting_space",
            "total_function_space",
        ):
            v = payload.get(k)
            if v is not None and str(v).strip():
                payload["square_footage"] = str(v).strip()
                break

    if not _nonempty_str(payload.get("square_footage")):
        for k in ("largest_meeting_room", "second_largest_meeting_room", "exhibit_space"):
            v = payload.get(k)
            if v is not None and str(v).strip():
                payload["square_footage"] = str(v).strip()
                break

    if not _nonempty_str(payload.get("square_footage")):
        payload["square_footage"] = "0 sq. ft."

    # --- int-like required fields: default "0" so sanitize_data_types can coerce ---
    intish_required = (
        "total_guest_rooms",
        "single_1_bed_rooms",
        "double_2_beds_rooms",
        "suite_rooms",
        "meeting_room_count",
        "max_capacity",
        "travelstar_rating",
    )
    for key in intish_required:
        val = payload.get(key)
        if val is None or val == "" or (isinstance(val, str) and not val.strip()):
            payload[key] = "0"

    return payload


def validate_schema(payload: Dict[str, Any], schema_cls: Any) -> Dict[str, Any]:
    """
    Validates the payload against the schema, enforcing required fields.
    Mutates the payload to match the validated data and strips extra fields.
    Returns the same payload reference.
    """
    validated_data = schema_cls.model_validate(payload).model_dump()

    payload.clear()
    payload.update(validated_data)

    return payload
