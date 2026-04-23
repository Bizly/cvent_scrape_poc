import re
import logging
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from services.http import HEADERS, throttled_get
from transforms.cleaning import (
    rename_property_fields,
    sanitize_data_types,
    validate_schema,
    format_airport_distances,
)
from models.venue import VenueDetailsSchema
from config import MAX_PAGES, CVENT_DOMAIN, CVENT_RESULTS_BASE_URL, CVENT_VENUE_PATH_FRAGMENT

logger = logging.getLogger(__name__)

# TODO: Phase 1 output (all_links) should write to DB with status='pending'
# so Phase 2 can query 'pending' records and resume without re-discovering links.


class CventScraper:

    # ---------------------------------------------------------------------------
    # Phase 1 — Link Discovery
    # ---------------------------------------------------------------------------

    def scrape_venue_links(self, country: str, city: str, max_pages: int = MAX_PAGES) -> list:
        """
        Scrape ALL venue detail page links from a Cvent city listing page,
        handling pagination and throttling safely.
        """

        city_url = urljoin(CVENT_RESULTS_BASE_URL, city)

        logger.info(f"Scraping venue links from {city_url}")

        session = requests.Session()

        venue_links = set()
        page = 1
        empty_pages = 0

        while page <= max_pages:
            page_url = city_url if page == 1 else f"{city_url}?page={page}"
            logger.info(f"  → Page {page}: {page_url}")

            resp = throttled_get(session, page_url)
            soup = BeautifulSoup(resp.text, "lxml")

            page_links = set()

            for a in soup.select("a[href]"):
                href = a["href"]
                if CVENT_VENUE_PATH_FRAGMENT in href:
                    page_links.add(urljoin(CVENT_DOMAIN, href))

            if not page_links:
                empty_pages += 1
            else:
                empty_pages = 0

            new_links = page_links - venue_links
            venue_links.update(page_links)

            logger.info(f"    Found {len(page_links)} venues ({len(new_links)} new)")

            # Stop conditions
            if empty_pages >= 2:
                logger.info("    Stopping: no venues found on consecutive pages")
                break

            if not new_links:
                logger.info("    Stopping: no new venues discovered")
                break

            page += 1

        return list(venue_links)

    # ---------------------------------------------------------------------------
    # Phase 2 — Venue Detail Scraping
    # ---------------------------------------------------------------------------

    def scrape_venue_details(self, venue_url: str, country: str = None, city: str = None) -> dict:
        """
        Scrape detailed venue information from a Cvent venue page
        using DOM-anchored extraction with safe fallbacks.
        """

        session = requests.Session()
        session.headers.update(HEADERS)
        resp = throttled_get(session, venue_url)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        data = {
            "country": country,
            "city": city,
        }

        page_text = soup.get_text(" ", strip=True)

        # -----------------Step 1 - Scraping--------------------#
        self._scrape_venue_details_section(soup, data, venue_url)
        self._scrape_meeting_space_details(soup, data)
        self._scrape_guest_room_details(soup, data)
        self._scrape_built_renovated_years(page_text, data)
        self._scrape_location_and_parking(soup, data)
        self._scrape_facilities(soup, data)
        self._scrape_industry_ratings(soup, data)
        self._scrape_max_guest_capacity(soup, data)

        # -----------------Step 2 - Data Cleaning and Formatting --------------------#
        rename_property_fields(data)
        sanitize_data_types(data)
        validate_schema(data, VenueDetailsSchema)

        return data

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    def _scrape_venue_details_section(self, soup: BeautifulSoup, data: dict, venue_url: str):
        """
        Scrapes venue details like name, venue ID, and address.
        """

        # -------- Venue name --------
        h1_tag = soup.find("h1")
        if h1_tag:
            data["name"] = h1_tag.get_text(strip=True)
        else:
            parsed_url = urlparse(venue_url)
            path_segments = [s for s in parsed_url.path.split('/') if s]

            if len(path_segments) >= 6 and path_segments[0] == 'venues' and path_segments[1] == 'en-US':
                venue_name_slug = path_segments[5]
                if not re.match(
                    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
                    venue_name_slug
                ):
                    data["name"] = venue_name_slug.replace('-', ' ').title()

        # -------- Venue ID (UUID) --------
        uuid_match = re.search(
            r'venue-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})',
            venue_url
        )
        if uuid_match:
            data["venue_id"] = uuid_match.group(1)

        # -------- Address extraction --------
        address = None

        address_span = soup.find(
            "span",
            class_="line-clamp-2 sm:line-clamp-1 pl-[2px] text-b-md"
        )

        if address_span:
            address = address_span.get_text(strip=True)

        if address:
            data["address"] = address

        return data

    def _scrape_guest_room_details(self, soup, data: dict):
        """
        Scrapes guest room details from the Cvent Guest Rooms section.
        Values are returned as cleaned strings (no numeric casting).
        """

        # 1. Scope to guest rooms section
        section = soup.find("div", id="_guest_rooms")
        if not section:
            return

        # 2. Prefer desktop, fallback to mobile
        detail_container = (
            section.find("div", attrs={"data-cvent-id": "guest-room-detail"})
            or section.find("div", attrs={"data-cvent-id": "guest-room-detail-mobile"})
        )

        if not detail_container:
            return

        # 3. Metric blocks = divs with exactly two direct children
        metric_blocks = detail_container.find_all(
            lambda tag: tag.name == "div" and len(tag.find_all("div", recursive=False)) == 2
        )

        for block in metric_blocks:
            label_div, value_div = block.find_all("div", recursive=False)

            label = label_div.get_text(strip=True)
            value = value_div.get_text(" ", strip=True)

            if not label or not value:
                continue

            key = (
                label.lower()
                .replace(" ", "_")
                .replace("(", "")
                .replace(")", "")
            )

            # Store value exactly as shown (string)
            data[key] = value

    def _scrape_meeting_space_details(self, soup, data: dict):
        """
        Scrapes meeting space summary details from the Meeting Rooms section.
        Values are returned as cleaned strings (no numeric casting).
        """

        section = soup.find("div", id="_meeting_space")
        if not section:
            return

        # Prefer desktop, fallback to mobile
        container = (
            section.find("div", attrs={"data-cvent-id": "meeting-space-section-meeting-space-container"})
            or section.find("div", attrs={"data-cvent-id": "meeting-space-section-meeting-space-container-mobile"})
        )

        if not container:
            return

        # Only divs with exactly two direct children: label + value
        metric_blocks = container.find_all(
            lambda tag: tag.name == "div" and len(tag.find_all("div", recursive=False)) == 2
        )

        for block in metric_blocks:
            label_div, value_div = block.find_all("div", recursive=False)

            label = label_div.get_text(strip=True)
            value = value_div.get_text(" ", strip=True)

            if not label or not value:
                continue

            key = (
                label.lower()
                .replace(" ", "_")
                .replace("(", "")
                .replace(")", "")
            )

            # Normalize whitespace only — keep value intact
            data[key] = value

    def _scrape_built_renovated_years(self, page_text: str, data: dict):
        """
        Scrapes year built and renovated.
        """
        built_match = re.search(r"Built\s*(\d{4})", page_text)
        renovated_match = re.search(r"Renovated\s*(\d{4})", page_text)

        if built_match:
            data["year_built"] = built_match.group(1)
        if renovated_match:
            data["year_renovated"] = renovated_match.group(1)

    def _scrape_location_and_parking(self, soup, data: dict):
        """
        Scrapes airport distance strings and parking types.
        Enriches distance-only values with airport names when possible.
        Populates the 'airport_distances' and 'parking_types' lists in the data dict.
        """

        container = soup.find("div", id="_getting_here") or soup.find("div", id="_location")
        if not container:
            return

        # Initialize lists if they don't exist
        if "airport_distances" not in data or not isinstance(data["airport_distances"], list):
            data["airport_distances"] = []
        if "parking_types" not in data or not isinstance(data["parking_types"], list):
            data["parking_types"] = []

        # -------------------------------------------------
        # Collect descriptive text to mine airport names
        # -------------------------------------------------
        descriptive_text = " ".join(
            div.get_text(" ", strip=True)
            for div in container.find_all("div", class_="whitespace-pre-wrap")
        )

        # Find airport name candidates
        airport_name_matches = re.findall(
            r"([A-Z][A-Za-z\s]+Airport(?:\s*\([A-Z]{3}\))?)",
            descriptive_text
        )

        # -------------------------------------------------
        # Airport distances
        # -------------------------------------------------
        raw_airport_distances = []  # Collect raw distances here first
        for label in container.find_all("div"):
            if label.get_text(strip=True).lower() == "distance from airport":

                parent = label.find_parent("div")
                if not parent:
                    continue

                distances = []

                # Desktop values
                for dv in parent.find_all("div", class_="hidden sm:flex"):
                    text = dv.get_text(strip=True)
                    if "mi" in text:
                        distances.append(text)

                # Mobile fallback
                if not distances:
                    for mv in parent.find_all("div", class_="flex"):
                        text = mv.get_text(strip=True)
                        if "mi" in text:
                            distances.append(text)

                for dist in distances:
                    # Already contains airport name
                    if "from" in dist.lower():
                        raw_airport_distances.append(dist)
                    else:
                        # Try enriching with airport name
                        if airport_name_matches:
                            enriched = f"{dist} from {airport_name_matches[0]}"
                            raw_airport_distances.append(enriched)
                        else:
                            raw_airport_distances.append(dist)

        # Format and overwrite data["airport_distances"]
        data["airport_distances"] = format_airport_distances(list(dict.fromkeys(raw_airport_distances)))

        # -------------------------------------------------
        # Parking types
        # -------------------------------------------------
        for label in container.find_all("div"):
            if label.get_text(strip=True).lower() == "parking in the area":

                parent = label.find_parent("div")
                if not parent:
                    continue

                for row in parent.find_all("div", class_="leading-6"):
                    parking_type_div = row.find("div")
                    if parking_type_div:
                        data["parking_types"].append(
                            parking_type_div.get_text(strip=True)
                        )

        # Deduplicate while preserving order
        data["parking_types"] = list(dict.fromkeys(data["parking_types"]))

    def _scrape_facilities(self, soup: BeautifulSoup, data: dict):
        """
        Scrapes available facilities.
        """
        facilities_header = soup.find(string=re.compile("Facilities", re.I))
        if facilities_header:
            ul = facilities_header.find_parent().find_next("ul")
            if ul:
                facilities_list = [li.get_text(strip=True) for li in ul.find_all("li")]
                data["facilities"] = facilities_list

                # Check for catering and AV
                data["catering"] = any(re.search(r'catering', f, re.I) for f in facilities_list)
                data["av"] = any(re.search(r'audiovisual|av', f, re.I) for f in facilities_list)

    def _scrape_industry_ratings(self, soup, data: dict):
        """
        Scrapes industry ratings (Northstar, AAA, Forbes, etc.)
        and populates them as string values like '4 Star' in the data dict.
        """

        # Initialize industry_ratings as a dictionary if it doesn't exist
        if "industry_ratings" not in data or not isinstance(data["industry_ratings"], dict):
            data["industry_ratings"] = {}

        container = soup.find(
            "div",
            attrs={
                "data-cvent-id": "overview_section_industry_ratings_container"
            }
        )

        if not container:
            return

        rating_blocks = container.find_all("div", class_="flex flex-col")

        for block in rating_blocks:
            name_span = block.find("span")
            if not name_span:
                continue

            rating_name = name_span.get_text(strip=True)

            # Count filled icons (stars or diamonds)
            filled_icons = block.find_all(
                "svg",
                attrs={
                    "data-cvent-id": [
                        "star-fill-icon",
                        "diamond-filled-icon"
                    ]
                }
            )

            rating_value = len(filled_icons)
            if rating_value > 0:
                data["industry_ratings"][rating_name] = f"{rating_value} Star"

                # Store as string for later transformation
                if rating_name == 'Northstar':
                    data['travelstar_rating'] = f"{rating_value}"
                elif rating_name == 'AAA':
                    data['aaa_rating'] = f"{rating_value}"
                elif rating_name == 'Forbes Travel Guide':
                    data['forbes_rating'] = f"{rating_value}"

    def _scrape_max_guest_capacity(self, soup: BeautifulSoup, data: dict):
        """
        Scrapes maximum guest capacity from tables.
        """
        capacities = []
        for cell in soup.select("table td"):
            value = cell.get_text(strip=True).replace(",", "")
            if value.isdigit():
                capacities.append(int(value))

        if capacities:
            data["max_capacity"] = max(capacities)
