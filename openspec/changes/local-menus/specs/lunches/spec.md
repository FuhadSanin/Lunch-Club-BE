# Delta for Lunches

## ADDED Requirements

### Requirement: Local menus
The system SHALL let a host keep menus for restaurants that are not on Swiggy, offer them when starting a party,
and keep a party's copy unchanged when the menu later changes.

#### Scenario: A menu needs a name and a priced dish
- **WHEN** a host saves a menu without a name, or without a dish that has a name and a price
- **THEN** the response is 400 and nothing is saved

#### Scenario: Only the owner
- **GIVEN** a menu kept by one host
- **WHEN** another host, or a member, asks for it or tries to offer it
- **THEN** they do not get it (404, 403, or 400 when offering)

#### Scenario: Offered in a party
- **WHEN** a host starts a party with `local_menu_ids`
- **THEN** the party's offered restaurants include each menu with its dishes and prices, marked local
- **AND** changing the menu afterwards leaves the party's copy as it was

#### Scenario: Ordering a local restaurant
- **GIVEN** picks from a local restaurant and from a Swiggy one
- **WHEN** the picks are locked
- **THEN** the local restaurant has an order of its own
- **AND** placing it asks the host to order directly with the restaurant instead of calling Swiggy
