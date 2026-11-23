---
id: FEAT-001
title: Seat removal
module: MOD-billing
requirements:
- REQ-F-002
claims:
- id: FEAT-001.C1
  claim: Removing a seat issues no refund.
  requirement: REQ-F-002
  code: services/billing/seats.py#remove_seat
  test: tests/test_seats.py::test_no_refund_on_removal
---
Removing a seat leaves it active until the end of the billing cycle and issues no refund.
