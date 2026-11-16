# Delta for Accounts

## ADDED Requirements

### Requirement: Connecting an AI assistant
The system SHALL let an AI assistant act as a person only after that person has signed it
in (OAuth with PKCE, ending on a consent page where they press Allow), SHALL NOT offer any
other way to make an access token, and SHALL let the person list the assistants signed in
as them and disconnect any of them.

#### Scenario: No token by hand
- **WHEN** a signed-in person sends `POST /api/me/tokens`
- **THEN** the response is 405 and no token is made

#### Scenario: Listing
- **GIVEN** an assistant that was allowed by the person
- **WHEN** the person calls `GET /api/me/tokens`
- **THEN** it is listed by name with when it connected and when it was last used
- **AND** the token itself is not in the answer

#### Scenario: Disconnecting
- **GIVEN** an assistant holding an access token and a refresh token
- **WHEN** the person calls `DELETE /api/me/tokens/<id>` for it
- **THEN** the access token stops working at once
- **AND** trading the refresh token for new tokens is refused
- **AND** the assistant is no longer listed

#### Scenario: Someone else's assistant
- **WHEN** a person disconnects an assistant that is signed in as someone else
- **THEN** the response is 404 and that assistant keeps working
