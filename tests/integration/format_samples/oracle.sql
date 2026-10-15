SELECT o.order_id, o."OrderDate", c.name customer_name,
       NVL(SUM(l.qty*l.unit_price),0) total
  FROM sales.orders o, sales.customers c, sales.order_lines l
 WHERE c.customer_id = o.customer_id
   AND l.order_id(+) = o.order_id
   AND o."OrderDate" >= ADD_MONTHS(TRUNC(SYSDATE), -1)
   AND c.name <> 'O''Brien'
 GROUP BY o.order_id, o."OrderDate", c.name
 ORDER BY total DESC
 FETCH FIRST 10 ROWS ONLY
