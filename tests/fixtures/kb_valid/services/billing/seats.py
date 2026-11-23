def remove_seat(account, seat):
    """Deactivate a seat at the end of the billing cycle. No refund."""  # r[impl FEAT-001.C1]
    account.pending_removals.append(seat)
