# This is communicating with a local server that handles Tablecheck Distribution API, for details please contact api@tablecheck.com
import requests

TABLECHECK_BASE_URL = "http://10.69.33.216:3000/api/v1"


def list_shops() -> list[dict]:
    r = requests.get(f"{TABLECHECK_BASE_URL}/shops", timeout=10)
    r.raise_for_status()
    return r.json().get("shops", [])


def get_availability(shop_id: str, date: str, pax: int) -> list[str]:
    r = requests.get(
        f"{TABLECHECK_BASE_URL}/shops/{shop_id}/availability",
        params={"date": date, "pax": pax},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("slots", [])


def create_reservation(
    shop_id: str,
    start_at: str,
    pax: int,
    last_name: str,
    first_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    special_request: str | None = None,
) -> dict:
    body: dict = {"shop_id": shop_id, "start_at": start_at, "pax": pax, "last_name": last_name}
    if first_name:
        body["first_name"] = first_name
    if email:
        body["email"] = email
    if phone:
        body["phone"] = phone
    if special_request:
        body["special_request"] = special_request
    r = requests.post(f"{TABLECHECK_BASE_URL}/reservations", json=body, timeout=10)
    r.raise_for_status()
    return r.json().get("reservation", {})


def list_reservations() -> list[dict]:
    r = requests.get(f"{TABLECHECK_BASE_URL}/reservations", timeout=10)
    r.raise_for_status()
    return r.json().get("reservations", [])


def get_reservation(reservation_id: str) -> dict:
    r = requests.get(f"{TABLECHECK_BASE_URL}/reservations/{reservation_id}", timeout=10)
    r.raise_for_status()
    return r.json().get("reservation", {})


def update_reservation(reservation_id: str, special_request: str) -> dict:
    r = requests.patch(
        f"{TABLECHECK_BASE_URL}/reservations/{reservation_id}",
        json={"special_request": special_request},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("reservation", {})


def cancel_reservation(reservation_id: str) -> dict:
    r = requests.delete(f"{TABLECHECK_BASE_URL}/reservations/{reservation_id}", timeout=10)
    r.raise_for_status()
    return r.json()
