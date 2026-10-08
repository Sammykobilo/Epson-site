# Epson Centre

## Run locally

The site and payment API use only the Python standard library:

1. Copy `.env.example` to `.env` and fill in provider settings.
2. Put the product archives listed below in `private-products/`. Keep this directory private and do not commit customer-ready resetter packages to Git.
3. Start the site with `py server.py` on Windows, or `python3 server.py` on other systems.
4. Open `http://localhost:8000`.

## Crawlable blog pages and sitemap

Blog articles are published as static pages under `/blog/{slug}/`, with a static `/blog/` index, `sitemap.xml`, and `robots.txt`. The pages are generated from the `blogPosts` data in `index.html`:

```powershell
py scripts/generate_blog_pages.py
```

The generator defaults to `https://epson-site.vercel.app` for canonical links and sitemap entries. If the site uses a different Vercel or custom domain, set `SITE_ORIGIN` to that HTTPS origin when generating, for example `$env:SITE_ORIGIN = "https://www.example.com"`. Run the generator after editing the blog data and commit the generated pages and sitemap with the source change. The blog cards and navigation link to these crawlable article URLs.

The server refuses a checkout request for a product whose archive is missing or invalid. Archives are stored outside the public website routes and attached to the order email only after the payment provider confirms payment. If a cart contains more than one model that shares a program package, that common archive is attached only once.

### Shared model packages currently configured

| Private archive filename | Product models |
| --- | --- |
| `tx550w-sx510w.rar` | TX550W, SX510W |
| `l3110-l3111.zip` | L3110, L3111 |
| `l130-l220-l310-l360-l365.zip` | L130, L220, L310, L360, L365 |
| `l200.zip` | L200 |

The L3110/L3111 package is taken from the unencrypted L3110-L3111-L3315 ZIP. The separate password-protected L3110/L3111 ZIP had the same file sizes and CRCs for all 13 files, so it is not needed for delivery. The TX550W/SX510W source was supplied as RAR, so its original RAR is attached as-is. Although the archive contains a `TX550` folder, the package has been confirmed to work with both TX550W and SX510W printers.

Models without a shared package mapping still use `{product-slug}.zip`. The L3110-L3111-L3315 archive lists L3315, but this site does not currently offer an L3315 product page.

Each product detail page includes original model-labelled adjustment-program artwork and a five-stage illustrated waste-ink-counter click path: select printer/port, choose the waste-ink item from the adjustment list, check and review the counter, then confirm initialization after appropriate physical pad service. The drawings use the reference utility's broad visual structure (green work area, blue instruction panel, maintenance list, counter fields, and confirmation dialog) but are original schematics, not software screenshots; counter readings are placeholders. The written sequence is based on the published Epson L3110 walkthrough; labels on other model pages do not confirm that a particular utility version supports those models. Verify model compatibility and menu wording before use.

## Payment setup

- **NOWPayments:** configure an API key and IPN secret. Set `SITE_URL` to the public HTTPS URL reachable by NOWPayments. Its signed IPN is checked and the provider payment is looked up before fulfillment.
- **M-PESA:** the default environment is Daraja sandbox. Configure the sandbox credentials and use a public HTTPS callback URL routed to `/api/webhooks/mpesa`. M-PESA order totals are converted from USD to KES using the live USD/KES rate returned when the order begins; that rate and rounded KES amount are saved to the order.
- **Email:** configure SMTP with TLS and a sender address. Email credentials must remain server-side in `.env`.

Payment initiation and callback endpoints require a reachable Python server. The static-file preview (`file://`) is not a checkout environment. Vercel currently serves the static storefront only: the Python payment/email backend and the ignored files in `private-products/` are not included in the Git deployment. To enable live automatic delivery, deploy the Python backend separately and securely provision these exact private archive filenames on that backend (or move the mapping to a private object store). Do not switch to production payment credentials until provider callbacks, archive delivery, and email have been tested end to end.

## Product archive names

For a product without a shared-package mapping, use one ZIP file for each model:

`{product-slug}.zip`, where the product slug is the part after `/products/` in the product page URL.

Keep archives out of source control. Do not place payment credentials or customer/order data in public website files.
