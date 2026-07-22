#!/usr/bin/env python3
"""Build a minimal installable VSIX using only Python's standard library."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "vscode-extension"
DIST = ROOT / "dist"
FILES = (
    "package.json",
    "extension.js",
    "README.md",
    "media/here.svg",
    "media/here.png",
)


def manifest(package: dict) -> str:
    publisher = escape(str(package["publisher"]))
    name = escape(str(package["name"]))
    display_name = escape(str(package["displayName"]))
    description = escape(str(package["description"]))
    version = escape(str(package["version"]))
    engine = escape(str(package["engines"]["vscode"]))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}" />
    <DisplayName>{display_name}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Categories>Other,SCM Providers</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{engine}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace,ui" />
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code" /></Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Icons.Default" Path="extension/media/here.png" Addressable="true" />
  </Assets>
</PackageManifest>
"""


def content_types() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json" />
  <Default Extension="js" ContentType="application/javascript" />
  <Default Extension="md" ContentType="text/markdown" />
  <Default Extension="svg" ContentType="image/svg+xml" />
  <Default Extension="xml" ContentType="application/xml" />
  <Override PartName="/extension.vsixmanifest" ContentType="text/xml" />
</Types>
"""


def build() -> Path:
    package = json.loads((SOURCE / "package.json").read_text(encoding="utf-8"))
    version = str(package["version"])
    output = DIST / f"hereassistant-vscode-{version}.vsix"
    DIST.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types())
        archive.writestr("extension.vsixmanifest", manifest(package))
        for relative in FILES:
            archive.write(SOURCE / relative, f"extension/{relative}")
    return output


if __name__ == "__main__":
    print(build())
