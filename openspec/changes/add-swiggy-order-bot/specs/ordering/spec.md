# Delta for Ordering

## ADDED Requirements

### Requirement: Linking a Swiggy account
A host SHALL be able to link their Swiggy account once with their phone number and an
OTP. The system MUST store only the resulting login session, encrypted, and MUST never
send it to the browser. The host SHALL be able to unlink at any time.

#### Scenario: Host links Swiggy
- **WHEN** a host enters their phone number and the OTP Swiggy sends
- **THEN** their account shows Swiggy as linked
- **AND** no password or OTP is stored

#### Scenario: Login has expired
- **GIVEN** a linked account whose Swiggy login has expired
- **WHEN** an order is placed
- **THEN** the checkout is `needs_host` and the host is asked to link Swiggy again

### Requirement: Automatic checkout on the Swiggy website
For a host with a linked account, the system SHALL place each confirmed checkout on the
Swiggy website as one order on the host's account, and MUST hand the order to the host
instead of paying when the cart total differs from the lunch's total.

#### Scenario: Normal checkout
- **WHEN** a confirmed checkout with 3 restaurants is placed
- **THEN** one Swiggy order exists on the host's account and the checkout is `placed` with its number

#### Scenario: Price changed
- **WHEN** Swiggy's cart total differs from the checkout total
- **THEN** nothing is paid and the checkout is `needs_host` with the difference explained
