// The catalogue, priced in the browser. Nothing here is in the HTML: the shell is empty
// until this has run.
(async function () {
  const app = document.getElementById('app');
  const response = await fetch('/api/catalog.json');
  const catalog = await response.json();
  const rules = catalog.rules;
  const cents = (value) => Math.round(value * 100) / 100;

  const lines = catalog.items.map((item) => {
    let unit = item.price;
    const discount = rules.discounts.find(
      (d) => d.category === item.category && item.qty >= d.min_qty
    );
    if (discount) unit = cents(unit * (1 - discount.rate));
    return { name: item.name, qty: item.qty, unit: unit, total: cents(unit * item.qty) };
  });
  const subtotal = cents(lines.reduce((sum, line) => sum + line.total, 0));
  const shipping = subtotal >= rules.free_shipping_from ? 0 : rules.shipping;
  const grand = cents(subtotal + shipping);

  const heading = document.createElement('h1');
  heading.textContent = catalog.shop + ': ' + lines.length + ' items';
  const list = document.createElement('ul');
  list.id = 'lines';
  for (const line of lines) {
    const row = document.createElement('li');
    row.textContent =
      line.name + ': ' + line.qty + ' x ' + line.unit.toFixed(2) + ' = ' + line.total.toFixed(2);
    list.appendChild(row);
  }
  const sub = document.createElement('p');
  sub.id = 'subtotal';
  sub.textContent = 'Subtotal: ' + subtotal.toFixed(2);
  const ship = document.createElement('p');
  ship.id = 'shipping';
  ship.textContent = 'Shipping: ' + shipping.toFixed(2);
  const total = document.createElement('p');
  total.id = 'total';
  total.textContent = 'Grand total: ' + grand.toFixed(2);
  app.append(heading, list, sub, ship, total);
})();
