CREATE TABLE `shop`.`Orders` (
  `order_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `CustomerId` INT NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `Status` ENUM('new','paid','void') NOT NULL DEFAULT 'new',
  PRIMARY KEY (`order_id`),
  KEY `IX_Orders_Customer` (`CustomerId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
