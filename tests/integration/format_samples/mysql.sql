SELECT `o`.`order_id`, o.created_at, c.`Name` AS customer_name,
    IFNULL(SUM(l.qty*l.unit_price),0) AS total, JSON_UNQUOTE(o.meta->'$.kind') AS kind
FROM `shop`.`Orders` o
JOIN shop.Customers c ON c.customer_id=o.customer_id
LEFT JOIN shop.order_lines l USING (order_id)
WHERE o.created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND c.`Name` LIKE 'A%'
GROUP BY `o`.`order_id`, o.created_at, c.`Name`
ORDER BY total DESC LIMIT 10, 20;
