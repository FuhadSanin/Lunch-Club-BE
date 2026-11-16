# Delta for Ordering

## MODIFIED Requirements

### Requirement: Handing an order to the host
When an order can't be placed automatically, the system SHALL mark it `needs_host`,
explain what to do, email the host, and let anyone with `order_update` enter the Swiggy
order number once they have checked out in the Swiggy app.

#### Scenario: No automatic ordering configured
- **WHEN** a confirmed checkout is placed with the manual provider
- **THEN** it is `needs_host` with instructions, and the host is emailed a link to the lunch

#### Scenario: Automatic ordering fails
- **WHEN** the ordering attempt raises an error
- **THEN** the checkout is `needs_host` and the host is told to check the Swiggy app first in case the order went through

#### Scenario: Entering the order number
- **WHEN** someone with `order_update` posts the platform's order number (`platform_order_id`)
  for a `needs_host` checkout
- **THEN** it is `placed` with that number

#### Scenario: Entering a number for an order that isn't waiting
- **WHEN** someone posts an order number for a checkout that is not `needs_host`
- **THEN** the system responds 409
