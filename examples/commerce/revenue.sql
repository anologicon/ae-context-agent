-- Exemplo didático: uma linha por pedido confirmado.
select order_id, customer_id, amount
from orders
where status = 'completed' and not is_complimentary
