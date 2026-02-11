from __future__ import annotations

import re

ROOT_ID_BLOCKLIST = {"__next", "root", "app", "__nuxt", "gatsby-focus-wrapper"}
ROOT_ID_BLOCKLIST_LOWER = {item.lower() for item in ROOT_ID_BLOCKLIST}


def is_blocked_root_id(id_value: str) -> bool:
    return id_value.strip().lower() in ROOT_ID_BLOCKLIST_LOWER


def is_obvious_root_container_locator(locator: str) -> bool:
    value = locator.strip().lower()
    if not value:
        return False
    if value in {"html", "body"}:
        return True

    for root_id in ROOT_ID_BLOCKLIST_LOWER:
        if value == f"#{root_id}":
            return True
        if f'[id="{root_id}"]' in value or f"[id='{root_id}']" in value:
            return True
        if f"@id='{root_id}'" in value or f'@id="{root_id}"' in value:
            return True

    # Match selectors like div#__next, main #root, etc.
    if re.search(r"(^|[\\s>+~])#(?:__next|root|app|__nuxt|gatsby-focus-wrapper)(?=$|[\\s>+~\\[:.#])", value):
        return True
    return False
