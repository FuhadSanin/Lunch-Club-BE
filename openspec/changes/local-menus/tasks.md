# Tasks: Local menus

- [x] 1. API: `LocalMenu`, its endpoints and limits, `local_menu_ids` on starting a party, the wider `restaurant_id`, separate local orders handed to the host, `local` on carts, access token path
- [x] 2. MCP: `add_local_menu`, `list_local_menus`, `delete_local_menu`, `start_lunch(local_menu_ids)`
- [x] 3. Web app: Local menu page (chat way only when an assistant is connected; manual form with required fields in red), Local menu section when starting a party, badge and note while picking, local carts without a Swiggy link
- Verified by: `tests/test_local_menus.py` and the local menu tests in `tests/test_mcp.py`; `npm test`; a browser run against a throwaway API (manual add, assistant saves with its token and it appears, offered in a party, picked, saved against the local restaurant)
