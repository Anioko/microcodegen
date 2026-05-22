# SaaS Billing Platform

A subscription billing and customer management platform with Stripe integration.

## Entities

- Customer: name (string, required), email (string, required, unique), company (string), phone (string)
- Subscription: plan_id (string, required), status (string, required), current_period_start (datetime), current_period_end (datetime), cancel_at_period_end (boolean)
- Invoice: amount (integer, required), currency (string, required), status (string), paid_at (datetime), stripe_invoice_id (string)
- Plan: name (string, required), price_monthly (integer, required), price_annual (integer), features (text), is_active (boolean)
- PaymentMethod: type (string, required), last4 (string), brand (string), is_default (boolean)

## User Stories

- As a customer, I want to subscribe to a plan so that I can access the product.
- As a customer, I want to view my invoices so that I can track my spending.
- As a customer, I want to update my payment method so that my subscription stays active.
- As a customer, I want to cancel my subscription so that I stop being charged.
- As an admin, I want to see all active subscriptions so that I can track MRR.
- As an admin, I want to see failed payments so that I can follow up with customers.
- As an admin, I want to create new plans so that I can adjust pricing.

## Integrations

- Stripe (payment processing, webhooks)
- SendGrid (email notifications)
