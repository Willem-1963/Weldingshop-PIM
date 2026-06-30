# Release 0.3 - Product Viewer

Deze release voegt een eerste productviewer toe.

## Nieuwe commando's

Bekijk een product:

```powershell
py -m app.products.product_viewer SP81470
```

Zoek producten:

```powershell
py -m app.products.product_viewer --search ratel
```

Exporteer een product naar Markdown:

```powershell
py -m app.products.product_viewer SP81470 --export
```

## Test

```powershell
py -m app.products.product_viewer 193GE-10
py -m app.products.product_viewer --search SP81470
py -m app.core.dashboard
```
