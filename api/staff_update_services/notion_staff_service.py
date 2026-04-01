"""
Notion API operations for staff/contact person management.
"""

import requests
import re
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
from ..clients.notion_client import NotionClient
import logging

logging.basicConfig(level=logging.INFO)


def get_database_properties(notion: NotionClient) -> Optional[Dict[str, Any]]:
    """
    Gets property types from Notion database schema.
    """
    try:
        database_data = notion.get_database()
        return database_data.get("properties", {})
    except Exception as e:
        logging.error(f"Failed to fetch database properties: {e}")
        return None


def find_staff_page_by_name_and_role(
    notion: NotionClient, name: str, role: str, company_page_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """
    Finds a single existing staff page by person's name (title) and role.
    """
    try:
        filters = []

        if name:
            filters.append({"property": "Nimi", "title": {"equals": name}})

        if role:
            filters.append({"property": "Amet", "rich_text": {"contains": role}})

        if company_page_id:
            filters.append(
                {"property": "Organisatsioon", "relation": {"contains": company_page_id}}
            )

        if len(filters) < 2:
            return None

        existing_pages = notion.query_database({"and": filters})

        for page in existing_pages:
            if not page.get("archived", False):
                return page

        return None
    except Exception as e:
        logging.error(f"Error querying Notion database for existing staff: {e}")
        return None


def map_staff_to_properties(
    staff_member: Dict[str, Any], page_id: Optional[str]
) -> Dict[str, Any]:
    """
    Maps staff member data to flat Notion property dictionary.
    """
    properties_data = {
        "Nimi": staff_member.get("name"),
        "Amet": staff_member.get("role"),
        "E-mail": staff_member.get("email") if staff_member.get("email") else None,
        "Tel. nr": staff_member.get("phone") if staff_member.get("phone") else None,
    }

    if page_id:
        properties_data["Organisatsioon"] = page_id

    return properties_data


def build_notion_properties(
    properties_data: Dict[str, Any], page_properties: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Converts flat property data into Notion API format.
    """
    notion_properties = {}

    for prop_name, prop_value in properties_data.items():
        if prop_value is None:
            continue

        if prop_name == "Nimi":
            notion_properties[prop_name] = {
                "title": [{"text": {"content": prop_value}}]
            }
        elif prop_name == "Amet":
            notion_properties[prop_name] = {
                "rich_text": [{"text": {"content": prop_value}}]
            }
        elif prop_name == "E-mail":
            notion_properties[prop_name] = {"email": prop_value}
        elif prop_name == "Tel. nr":
            notion_properties[prop_name] = {"phone_number": prop_value}
        elif prop_name == "Organisatsioon":
            notion_properties[prop_name] = {"relation": [{"id": prop_value}]}

    return notion_properties


def sync_staff_data(
    notion: NotionClient,
    staff_data: List[Dict[str, Any]],
    page_id: Optional[str],
    database_id: str,
    page_properties: Optional[Dict[str, Any]],
) -> Tuple[int, int, int, int, List[str]]:
    """
    Synchronizes staff members with the following logic:
    1. Same name and role → update
    2. Same role, different name → expire old, create new
    3. New role → create
    """
    created_count = 0
    updated_count = 0
    failed_count = 0
    skipped_count = 0
    errors = []

    for staff_member in staff_data:
        person_name = staff_member.get("name")
        person_role = staff_member.get("role")

        if not person_role or (not person_name and person_role != "General Contact"):
            failed_count += 1
            errors.append(f"Puudulikud andmed: {staff_member}")
            continue

        try:
            existing_page = find_staff_page_by_name_and_role(
                notion, person_name, person_role, page_id
            )

            current_staff_member_data = staff_member.copy()

            if existing_page:
                existing_flat_data = extract_notion_properties_for_comparison(
                    existing_page
                )

                email_changed = (
                    current_staff_member_data.get("email")
                    != existing_flat_data.get("E-mail")
                )
                phone_changed = (
                    current_staff_member_data.get("phone")
                    != existing_flat_data.get("Tel. nr")
                )

                if email_changed or phone_changed:
                    notion.update_page(
                        existing_page["id"],
                        build_notion_properties(
                            map_staff_to_properties(current_staff_member_data, page_id),
                            page_properties,
                        ),
                    )
                    updated_count += 1
                else:
                    skipped_count += 1
            else:
                # No page with this name+role: same name + different role → we will create
                # a new page (no existing_role_page for the new role), or same role +
                # different name → create new. Never overwrite or delete the old page.
                existing_role_page = find_staff_page_by_role_only(
                    notion, person_role, page_id, exclude_aegunud=True
                )

                if existing_role_page:
                    existing_flat_data = extract_notion_properties_for_comparison(
                        existing_role_page
                    )
                    existing_name = existing_flat_data.get("Nimi")

                    if existing_name and existing_name != person_name:
                        # Different person with same role: mark previous holder as (endine), then add new
                        existing_role = existing_flat_data.get("Amet") or person_role
                        mark_page_as_endine(
                            notion,
                            existing_role_page["id"],
                            existing_role,
                        )
                        current_staff_member_data["role"] = person_role

                        notion.create_page(
                            {
                                "parent": {"database_id": database_id},
                                "properties": build_notion_properties(
                                    map_staff_to_properties(
                                        current_staff_member_data, page_id
                                    ),
                                    page_properties,
                                ),
                            }
                        )
                        created_count += 1
                    else:
                        # Same person, update existing page
                        notion.update_page(
                            existing_role_page["id"],
                            build_notion_properties(
                                map_staff_to_properties(
                                    current_staff_member_data, page_id
                                ),
                                page_properties,
                            ),
                        )
                        updated_count += 1
                else:
                    # Keep original role without date suffix
                    current_staff_member_data["role"] = person_role

                    notion.create_page(
                        {
                            "parent": {"database_id": database_id},
                            "properties": build_notion_properties(
                                map_staff_to_properties(
                                    current_staff_member_data, page_id
                                ),
                                page_properties,
                            ),
                        }
                    )
                    created_count += 1

        except requests.HTTPError as e:
            failed_count += 1
            errors.append(str(e))
        except Exception as e:
            failed_count += 1
            errors.append(str(e))

    return created_count, updated_count, failed_count, skipped_count, errors


def extract_notion_properties_for_comparison(page: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts comparable flat property values from a Notion page.
    """
    properties = page.get("properties", {})
    extracted = {}

    name_prop = properties.get("Nimi", {})
    extracted["Nimi"] = (
        name_prop["title"][0]["plain_text"]
        if name_prop.get("type") == "title" and name_prop.get("title")
        else None
    )

    role_prop = properties.get("Amet", {})
    extracted["Amet"] = (
        role_prop["rich_text"][0]["plain_text"]
        if role_prop.get("type") == "rich_text" and role_prop.get("rich_text")
        else None
    )

    email_prop = properties.get("E-mail", {})
    extracted["E-mail"] = email_prop.get("email")

    phone_prop = properties.get("Tel. nr", {})
    extracted["Tel. nr"] = phone_prop.get("phone_number")

    return extracted


def find_staff_page_by_role_only(
    notion: NotionClient,
    role: str,
    company_page_id: Optional[str] = None,
    exclude_aegunud: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Finds a staff page by role only.
    """
    try:
        filters = []

        if role:
            filters.append({"property": "Amet", "rich_text": {"contains": role}})

        if company_page_id:
            filters.append(
                {"property": "Organisatsioon", "relation": {"contains": company_page_id}}
            )

        if not filters:
            return None

        existing_pages = notion.query_database(
            {"and": filters} if len(filters) > 1 else filters[0]
        )

        for page in existing_pages:
            if page.get("archived", False):
                continue

            if exclude_aegunud:
                role_prop = page.get("properties", {}).get("Amet", {})
                if role_prop.get("type") == "rich_text" and role_prop.get("rich_text"):
                    plain = role_prop["rich_text"][0].get("plain_text", "").upper()
                    if "AEGUNUD" in plain or "ENDINE" in plain:
                        continue

            return page

        return None
    except Exception as e:
        logging.error(f"Error querying Notion database for staff by role: {e}")
        return None


def _base_role_for_suffix(role: str) -> str:
    """Strip (endine), AEGUNUD, and other parenthetical suffixes to get base role."""
    base = role
    for tag in ["AEGUNUD", "uuendatud", "Lisatud", "(endine)", "endine"]:
        base = base.replace(tag, "").strip()
    base = re.sub(r"\s*\([^)]*\)\s*", "", base).strip()
    return base or role


def mark_page_as_endine(notion: NotionClient, page_id: str, current_role: str) -> bool:
    """
    Marks a staff page as former holder of the role: e.g. "Finantsjuht" → "Finantsjuht (endine)".
    """
    try:
        base_role = _base_role_for_suffix(current_role)
        notion.update_page(
            page_id,
            {
                "Amet": {
                    "rich_text": [{"text": {"content": f"{base_role} (endine)"}}]
                }
            },
        )
        return True
    except Exception as e:
        logging.error(f"Error marking page as endine: {e}")
        return False


def mark_page_as_aegunud(notion: NotionClient, page_id: str, current_role: str) -> bool:
    """
    Marks a staff page as AEGUNUD by updating the role property.
    """
    try:
        base_role = _base_role_for_suffix(current_role)
        notion.update_page(
            page_id,
            {
                "Amet": {
                    "rich_text": [{"text": {"content": f"{base_role} AEGUNUD"}}]
                }
            },
        )
        return True
    except Exception as e:
        logging.error(f"Error marking page as AEGUNUD: {e}")
        return False
