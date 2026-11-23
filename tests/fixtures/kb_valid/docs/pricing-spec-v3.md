# Pricing Spec v3

## 3.1 Adding seats

Adding a seat mid-cycle is charged pro-rata for the remaining days of the billing cycle.

## 3.2 Removing seats

A removed seat stays active until the end of the billing cycle. No refund is given.

## 4. Invoices

All amounts are stored and calculated in integer cents.

## 5. Sessions

Sessions are kept in Redis. A session expires after 30 minutes of inactivity.
