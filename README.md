# Epson Centre

## Run locally

The site and payment API use only the Python standard library:

1. Copy `.env.example` to `.env` and fill in provider settings.
2. Put each product archive in `private-products/`, named exactly like its product slug, for example `epson-l3110-adjustment-program.zip`.
3. Start the site with `py server.py` on Windows, or `python3 server.py` on other systems.
4. Open `http://localhost:8000`.

## Crawlable blog pages and sitemap

Blog articles are published as static pages under `/blog/{slug}/`, with a static `/blog/` index, `sitemap.xml`, and `robots.txt`. The pages are generated from the `blogPosts` data in `index.html`:

```powershell
py scripts/generate_blog_pages.py
```

The generator defaults to `https://epson-site.vercel.app` for canonical links and sitemap entries. If the site uses a different Vercel or custom domain, set `SITE_ORIGIN` to that HTTPS origin when generating, for example `$env:SITE_ORIGIN = "https://www.example.com"`. Run the generator after editing the blog data and commit the generated pages and sitemap with the source change. The blog cards and navigation link to these crawlable article URLs.

The server refuses to start checkout for a product whose ZIP archive is missing. Archives are stored outside the public website routes and are attached to the order email only after the payment provider confirms payment.

Each product detail page includes original model-labelled adjustment-program artwork and a five-stage illustrated waste-ink-counter click path: select printer/port, choose the waste-ink item from the adjustment list, check and review the counter, then confirm initialization after appropriate physical pad service. The drawings use the reference utility's broad visual structure (green work area, blue instruction panel, maintenance list, counter fields, and confirmation dialog) but are original schematics, not software screenshots; counter readings are placeholders. The written sequence is based on the published Epson L3110 walkthrough; labels on other model pages do not confirm that a particular utility version supports those models. Verify model compatibility and menu wording before use.

## Payment setup

- **NOWPayments:** configure an API key and IPN secret. Set `SITE_URL` to the public HTTPS URL reachable by NOWPayments. Its signed IPN is checked and the provider payment is looked up before fulfillment.
- **M-PESA:** the default environment is Daraja sandbox. Configure the sandbox credentials and use a public HTTPS callback URL routed to `/api/webhooks/mpesa`. M-PESA order totals are converted from USD to KES using the live USD/KES rate returned when the order begins; that rate and rounded KES amount are saved to the order.
- **Email:** configure SMTP with TLS and a sender address. Email credentials must remain server-side in `.env`.

Payment initiation and callback endpoints require a reachable server. The static-file preview (`file://`) is not a checkout environment. Do not switch to production payment credentials until provider callbacks, ZIP delivery, and email have been tested end to end.

## Product archive names

Use one ZIP file for each model:

`{product-slug}.zip`, where the product slug is the part after `/products/` in the product page URL.

Keep archives out of source control. Do not place payment credentials or customer/order data in public website files.
