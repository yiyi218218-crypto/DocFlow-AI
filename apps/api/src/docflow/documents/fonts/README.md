# Bundled fonts

**NotoSansBengali.ttf** — Noto Sans Bengali, SIL Open Font License 1.1,
© The Noto Project Authors (https://github.com/notofonts/bengali).

Bundled rather than relying on a system font because the wedge is Bangladeshi
documents and rendering must be deterministic across a developer's macOS, the
Debian worker image and CI. Without a Bengali-capable face, Bangla text renders
as `?????` and the extracted text layer is worthless — which silently breaks the
one market this product is built for.
