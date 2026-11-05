"""Authorized Hyundai, Kia, and Volvo dealerships within 100 miles of ZIP 94550 (Livermore, CA)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DealershipInfo:
    """Authorized franchise dealer record relative to Livermore, CA (94550)."""

    name: str
    brand: str
    city: str
    corridor: str
    approx_miles_from_94550: int
    tactical_role: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEALERSHIPS_NEAR_94550: tuple[DealershipInfo, ...] = (
    # --- HYUNDAI (within 100 miles of 94550) ---
    DealershipInfo("Dublin Hyundai", "Hyundai", "Dublin, CA", "Tri-Valley / East Bay", 9, "Closest store for test drives & final local price-match"),
    DealershipInfo("Hyundai of Tracy", "Hyundai", "Tracy, CA", "Central Valley", 20, "Quick jump over Altamont Pass; good competing East-vs-Valley quote"),
    DealershipInfo("Fremont Hyundai", "Hyundai", "Fremont, CA", "East Bay", 22, "High EV volume along I-680/I-880 corridor"),
    DealershipInfo("San Leandro Hyundai", "Hyundai", "San Leandro, CA", "East Bay", 27, "East Bay volume competitor for multi-dealer bidding"),
    DealershipInfo("Concord Hyundai", "Hyundai", "Concord, CA", "East Bay / 680 North", 30, "Contra Costa store; cross-shop online EV stock"),
    DealershipInfo("Capitol Hyundai", "Hyundai", "San Jose, CA", "South Bay", 31, "Capitol Auto Mall volume store; strong month-end EV volume push"),
    DealershipInfo("Stevens Creek Hyundai", "Hyundai", "Santa Clara, CA", "South Bay", 33, "High-inventory Silicon Valley EV store"),
    DealershipInfo("Antioch Hyundai", "Hyundai", "Antioch, CA", "East Contra Costa", 34, "Often willing to beat inner-Bay dealer discounts to win Tri-Valley buyers"),
    DealershipInfo("San Bruno Hyundai", "Hyundai", "San Bruno, CA", "Peninsula", 44, "Peninsula alternative with frequent EV allocations"),
    DealershipInfo("Stockton Hyundai", "Hyundai", "Stockton, CA", "Central Valley", 46, "Central Valley lever to undercut Bay Area pricing"),
    DealershipInfo("Hyundai of Vacaville", "Hyundai", "Vacaville, CA", "I-80 / Solano", 54, "Midpoint I-80 store with strong volume incentives"),
    DealershipInfo("Central Valley Hyundai", "Hyundai", "Modesto, CA", "Central Valley", 54, "Strong MSRP discount benchmark away from Bay Area markups"),
    DealershipInfo("Manly Hyundai", "Hyundai", "Santa Rosa, CA", "North Bay", 86, "North Bay sleeper store with transparent internet quotes"),
    DealershipInfo("Elk Grove Hyundai", "Hyundai", "Elk Grove, CA", "Sacramento Metro", 76, "High-volume Sacramento-south dealer; aggressive internet fleet desk"),
    DealershipInfo("Roseville Hyundai", "Hyundai", "Roseville, CA", "Sacramento Metro", 96, "Roseville Automall giant; great end-of-month stretch target"),

    # --- KIA (within 100 miles of 94550) ---
    DealershipInfo("Dublin Kia", "Kia", "Dublin, CA", "Tri-Valley / East Bay", 9, "Closest store for EV9 test drives & local price-match"),
    DealershipInfo("Hayward Kia", "Kia", "Hayward, CA", "East Bay", 18, "Nearby East Bay quote anchor; scrutinize add-on packages"),
    DealershipInfo("Winn Kia of Fremont", "Kia", "Newark, CA", "East Bay", 21, "Strong EV9 allocation across Newark/Fremont corridor"),
    DealershipInfo("Concord Kia", "Kia", "Concord, CA", "East Bay / 680 North", 30, "Contra Costa competitor along I-680"),
    DealershipInfo("Capitol Kia", "Kia", "San Jose, CA", "South Bay", 31, "Capitol Expressway Auto Mall volume competitor"),
    DealershipInfo("Kia Country of Manteca", "Kia", "Manteca, CA", "Central Valley", 32, "Prime Central Valley dealer 30 min east; use to drive down MSRP"),
    DealershipInfo("Stevens Creek Kia", "Kia", "Santa Clara, CA", "South Bay", 33, "Large South Bay EV9 inventory; pit against Capitol & Manteca"),
    DealershipInfo("Oakland Kia", "Kia", "Oakland, CA", "East Bay", 34, "Inner East Bay inventory source for specific EV9 colors/trims"),
    DealershipInfo("Putnam Kia of Burlingame", "Kia", "Burlingame, CA", "Peninsula", 41, "Peninsula group store with regular EV9 Wind/Land/GT-Line stock"),
    DealershipInfo("Kia of Stockton", "Kia", "Stockton, CA", "Central Valley", 45, "Central Valley volume bidder; often cleaner on pre-incentive discounts"),
    DealershipInfo("Valley Kia of Modesto", "Kia", "Modesto, CA", "Central Valley", 56, "Key Central Valley anchor quote before closing locally"),
    DealershipInfo("Kia of Vacaville", "Kia", "Vacaville, CA", "I-80 / Solano", 58, "Straight up I-680/I-80; competitive internet department"),
    DealershipInfo("Elk Grove Kia", "Kia", "Elk Grove, CA", "Sacramento Metro", 76, "Major Sacramento volume store; aggressive on aged outgoing-MY EV9s"),
    DealershipInfo("Jim Bone Kia of Santa Rosa", "Kia", "Santa Rosa, CA", "North Bay", 87, "North Bay family store known for straightforward internet desk"),
    DealershipInfo("Folsom Lake Kia", "Kia", "Folsom, CA", "Sacramento Metro", 95, "High-volume Sac-east store inside the 100-mile boundary"),

    # --- VOLVO (within 100 miles of 94550) ---
    DealershipInfo("Volvo Cars Dublin", "Volvo", "Dublin, CA", "Tri-Valley / East Bay", 9, "Home-turf store for EX90 & XC90 T8 test drives and service convenience"),
    DealershipInfo("Volvo Cars Walnut Creek", "Volvo", "Walnut Creek, CA", "East Bay / 680 North", 26, "High-volume luxury East Bay store; frequently stocks XC90 T8 & EX90 loaners"),
    DealershipInfo("AutoNation Volvo Cars San Jose", "Volvo", "San Jose, CA", "South Bay", 33, "Large AutoNation allocation; straightforward online price sheets"),
    DealershipInfo("McKevitt Volvo Cars", "Volvo", "San Leandro, CA", "East Bay", 28, "East Bay competitor to pit directly against Dublin & Walnut Creek"),
    DealershipInfo("Volvo Cars Palo Alto", "Volvo", "Palo Alto, CA", "Peninsula", 36, "Very experienced with Corporate Volvo A-Plan & MSD lease structuring"),
    DealershipInfo("Putnam Volvo Cars Burlingame", "Volvo", "Burlingame, CA", "Peninsula", 42, "Peninsula store; strong luxury EV/PHEV stock"),
    DealershipInfo("Royal Motors Volvo", "Volvo", "San Francisco, CA", "San Francisco", 44, "SF luxury store; check aged EX90 Ultra / Plus inventory"),
    DealershipInfo("Volvo Cars Marin", "Volvo", "San Rafael, CA", "North Bay", 53, "High PHEV/EV buyer base; check retired courtesy loaner XC90 T8s"),
    DealershipInfo("Volvo Cars Monterey", "Volvo", "Seaside, CA", "Monterey Bay", 84, "Outside-the-bubble luxury store within 100 mi"),
    DealershipInfo("Niello Volvo Cars Sacramento", "Volvo", "Sacramento, CA", "Sacramento Metro", 86, "Premier Sacramento volume dealer; crucial non-Bay-Area price lever"),
    DealershipInfo("Smothers European Volvo", "Volvo", "Santa Rosa, CA", "North Bay", 88, "Reputable North Bay dealer often willing to give clean buy-rate MF quotes"),
)
