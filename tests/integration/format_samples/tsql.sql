CREATE PROCEDURE [dbo].[usp_GetOrders] @CustomerId INT, @Since DATETIME2 = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (50) o.[OrderId], o.OrderDate, c.[Name] AS CustomerName,
        ISNULL(SUM(l.Qty*l.UnitPrice), 0) AS Total
    FROM dbo.Orders AS o WITH (NOLOCK)
    INNER JOIN dbo.Customers c ON c.CustomerId=o.CustomerId
    LEFT JOIN dbo.OrderLines l ON l.OrderId = o.OrderId
    WHERE o.CustomerId = @CustomerId AND (@Since IS NULL OR o.OrderDate >= @Since) AND c.Name <> N'ทดสอบ'
    GROUP BY o.[OrderId], o.OrderDate, c.[Name]
    ORDER BY o.OrderDate DESC;
END
