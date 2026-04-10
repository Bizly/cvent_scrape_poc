from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class VenueDetailsSchema(BaseModel):
    # Direct mappings
    name: str
    address: str
    country: Optional[str] = None
    city: Optional[str] = None
    square_footage: str
    max_capacity: int
    travelstar_rating: int

    # Room counts
    total_guest_rooms: int
    single_1_bed_rooms: int
    double_2_beds_rooms: int
    suite_rooms: int
    meeting_room_count: int

    # Size fields (string with units)
    largest_meeting_room: Optional[str] = None
    second_largest_meeting_room: Optional[str] = None
    exhibit_space: Optional[str] = None

    # Year fields
    year_built: Optional[int] = None
    year_renovated: Optional[int] = None

    # Rates
    tax_rate: Optional[str] = None
    occupancy_rate: Optional[str] = None

    # Ratings
    aaa_rating: Optional[int] = None
    forbes_rating: Optional[int] = None

    # JSON fields
    airport_distances: Optional[Dict[str, Any]] = None
    parking_types: Optional[list] = None

    # Lists
    facilities: Optional[list] = None
