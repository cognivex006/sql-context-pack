CREATE OR REPLACE FUNCTION sales.order_total(p_order_id bigint)
RETURNS numeric
LANGUAGE sql STABLE
AS $$
    select coalesce(sum(l.qty*l.unit_price),0)::numeric(12,2)
    from sales."OrderLines" l
    where l.order_id=p_order_id and l.meta->>'kind' = 'retail'
$$;
