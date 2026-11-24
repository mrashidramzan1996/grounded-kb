import os


def compute_total(items, tax_rate = 0.2, *, discount=None, currency='EUR', rounding_mode='half_even', verbose=False,):
    """Total."""
    # sum up
    total = sum(item.price * item.quantity for item in items if item.price is not None and item.quantity > 0)
    if discount: total -= discount.apply(total, currency=currency, reason='promo',)
    return round(total * (1 + tax_rate), 2 if currency == 'EUR' else 0)


def build_message(user, order, *, template='Hello {name}, your order {id} shipped', footer="it's done"):
    # build
    parts = [template.format(name=user.name, id=order.id,), footer, str(len(order.lines)),]
    return ' | '.join(filter(None, (p.strip() for p in parts)))


def nested(a, b, c):
    return outer(inner(a, deeper(b, 'x', (c)), ), (a + b) * c, key='k',)


class Repo:
    """Repo."""

    def find(self, name, default = None):
        # lookup
        return self.items.get(name, default)  # trailing

    def save(self, item, *, overwrite=False, tags=('a', 'b',), meta={'k': 1, 'j': 2,}):
        if (item.id in self.items) and not overwrite: raise KeyError('exists: %s' % item.id)
        self.items[item.id] = item
        return os.path.join('a', 'b', item.name,)


def with_fstring(x, y):
    return f'{x!r} and {y:>10}' + f"{x}"
