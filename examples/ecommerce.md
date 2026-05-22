# E-Commerce Platform

An online store with product catalog, inventory management, and order processing.

## Entities

- Product: name (string, required), description (text), price (integer, required), sku (string, required, unique), stock_quantity (integer), is_active (boolean)
- Category: name (string, required), slug (string, required, unique), description (text)
- Order: status (string, required), total_amount (integer, required), shipping_address (text), tracking_number (string)
- OrderItem: quantity (integer, required), unit_price (integer, required)
- Cart: session_id (string), expires_at (datetime)
- CartItem: quantity (integer, required)
- Review: rating (integer, required), body (text), is_verified (boolean)

## User Stories

- As a shopper, I want to browse products by category so that I can find what I need.
- As a shopper, I want to add products to my cart so that I can buy multiple items at once.
- As a shopper, I want to place an order so that I can receive products.
- As a shopper, I want to track my order status so that I know when to expect delivery.
- As a shopper, I want to write a product review so that I can help other customers.
- As a merchant, I want to manage product inventory so that I can avoid overselling.
- As a merchant, I want to view all orders so that I can process shipments.
- As a merchant, I want to add new products so that I can expand my catalog.

## Integrations

- Stripe (payment processing)
- SendGrid (order confirmation emails)
