# Proposal: Local menus

## Why

Some of the places a group eats at aren't on Swiggy: the canteen down the street, a restaurant the group is
sitting in. A host had no way to offer them, and no way to turn a paper menu into a party except typing it all in.

## What Changes

- **A host keeps local menus.** `local_menus`: a restaurant name, a picture, a rating, a distance in km and its
  dishes (name, price, optional picture and veg mark). `GET/POST /api/local-menus`, `GET/PUT/DELETE
  /api/local-menus/<id>`: only the owner's; hosts (and someone with a public email) only; at most 30 each. The name
  and each dish's name and price are required; the rest is optional. Pictures are an https link or a small data URL.
- **Offering one in a party.** `POST /api/lunches` takes `local_menu_ids`. The server copies those menus into the
  party's `offered_restaurants` (id `local-<id>`, `local: true`, `items`), so everyone sees the same menu and a
  client can't invent a price. Later edits to the menu don't change a party already started.
- **Ordering.** A pick keeps `restaurant_id = local-<id>` (the column is wider). At lock, each local restaurant gets
  its own order, apart from Swiggy checkouts; placing it is handed to the host (no Swiggy call). The cart marks it
  `local` with no Swiggy link.
- **From an AI chat.** The MCP server gains `add_local_menu`, `list_local_menus`, `delete_local_menu`, and
  `start_lunch` takes `local_menu_ids`. An access token may reach `/api/local-menus`.
- **Web app (`../lunch-board`).** A Local menu page (the chat way is on only when an assistant is connected, the
  manual form is always there, required fields in red), a Local menu section when starting a party, and a Local
  menu badge on the restaurant while picking.

## Impact

- API: new table `local_menus` (migration `lunches/0005_local_menus`, also widens `order_items.restaurant_id`).
- Specs: lunches (local menus).
