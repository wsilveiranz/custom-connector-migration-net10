"""Offline, guarded .NET 10 configuration migration for NuGet-based Logic Apps
Standard projects that host custom built-in connectors."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

WORKFLOWS_PACKAGE = "Microsoft.Azure.Workflows.WebJobs.Extension"
FUNCTIONS_SDK_PACKAGE = "Microsoft.NET.Sdk.Functions"
REVIEWED = {
    WORKFLOWS_PACKAGE: "1.194.31",
    FUNCTIONS_SDK_PACKAGE: "4.5.0",
}
CONNECTOR_PINS = {
    "Microsoft.Extensions.DependencyInjection.Abstractions": "10.0.0",
    "Microsoft.Extensions.Primitives": "9.0.9",
}
SDK_VERSION = "10.0.403"
SOURCE_TFM, TARGET_TFM = "net8.0", "net10.0"
BUNDLE_KEYS = (
    "AzureFunctionsJobHost__extensionBundle__id",
    "AzureFunctionsJobHost__extensionBundle__version",
)
SKIP_DIRS = {"bin", "obj", ".git", "node_modules", ".vs", "skills", ".azurelogicapps"}
BUILD_PATH = re.compile(r'(bin(?:\\\\|/)(?:Debug|Release)(?:\\\\|/))net8\.0(?=\\\\|/|"|$)')
SCRIPT_SUFFIXES = {".ps1", ".psm1", ".sh", ".cmd", ".bat", ".yml", ".yaml"}


class MigrationError(Exception):
    pass


# ---------------------------------------------------------------- JSONC

class Jsonc:
    """Minimal JSONC parser that keeps token positions for surgical edits."""

    token = re.compile(
        r'\s+|//[^\r\n]*|/\*[\s\S]*?\*/|"(?:[^"\\\x00-\x1f]|\\.)*"'
        r'|[{}\[\]:,]|true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?'
    )

    def __init__(self, text):
        self.text, self.tokens, self.comments, self.index = text, [], [], 0
        position = 0
        while position < len(text):
            match = self.token.match(text, position)
            if not match:
                raise MigrationError("Invalid JSON/JSONC syntax; content withheld.")
            raw = match.group()
            if raw.startswith(("//", "/*")):
                self.comments.append(match.start())
            elif not raw.isspace():
                self.tokens.append((raw, match.start(), match.end()))
            position = match.end()
        self.root = self._parse()
        if self.index != len(self.tokens):
            raise MigrationError("Unexpected trailing JSON/JSONC content.")
        if not isinstance(self.root["value"], dict):
            raise MigrationError("Expected a JSON object at the root.")

    def _take(self, expected=None):
        if self.index >= len(self.tokens):
            raise MigrationError("Incomplete JSON/JSONC content.")
        token = self.tokens[self.index]
        self.index += 1
        if expected is not None and token[0] != expected:
            raise MigrationError("Invalid JSON/JSONC structure.")
        return token

    def _peek(self):
        return self.tokens[self.index][0] if self.index < len(self.tokens) else None

    def _parse(self):
        raw, start, end = self._take()
        if raw not in ("{", "["):
            try:
                return {"value": json.loads(raw), "start": start, "end": end}
            except json.JSONDecodeError as error:
                raise MigrationError("Invalid JSON value; content withheld.") from error
        is_object = raw == "{"
        close = "}" if is_object else "]"
        members, values = [], ({} if is_object else [])
        while self._peek() != close:
            key, key_start = None, None
            if is_object:
                key_raw, key_start, _ = self._take()
                if not key_raw.startswith('"'):
                    raise MigrationError("JSON object keys must be strings.")
                key = json.loads(key_raw)
                if key in values:
                    raise MigrationError("Duplicate JSON keys require manual review.")
                self._take(":")
            child = self._parse()
            members.append({"key": key, "key_start": key_start, "node": child})
            if is_object:
                values[key] = child["value"]
            else:
                values.append(child["value"])
            if self._peek() == close:
                break
            self._take(",")
        _, _, end = self._take(close)
        return {"value": values, "start": start, "end": end, "members": members}

    def member(self, path):
        parent, node, found = None, self.root, None
        for key in path:
            members = node.get("members") or []
            found = next((m for m in members if m["key"] == key), None)
            if found is None:
                return None, None
            parent, node = node, found["node"]
        return parent, found

    def get(self, path):
        _, found = self.member(path)
        return None if found is None else found["node"]["value"]

    def set_string(self, path, value):
        _, found = self.member(path)
        if found is None:
            raise MigrationError("Expected setting is missing.")
        node = found["node"]
        return self.text[:node["start"]] + json.dumps(value) + self.text[node["end"]:]

    def remove(self, path):
        parent, found = self.member(path)
        if found is None:
            return self.text
        members = parent["members"]
        index = members.index(found)
        if index > 0:
            start, end = members[index - 1]["node"]["end"], found["node"]["end"]
        elif len(members) > 1:
            start, end = found["key_start"], members[1]["key_start"]
        else:
            start, end = parent["start"] + 1, parent["end"] - 1
        if any(start <= c < end for c in self.comments):
            raise MigrationError("A comment touches a member to remove; edit manually.")
        return self.text[:start] + self.text[end:]


def edit_json(text, operation, path, value=None):
    document = Jsonc(text)
    text = document.set_string(path, value) if operation == "set" else document.remove(path)
    Jsonc(text)
    return text


# ---------------------------------------------------------------- helpers

def version_tuple(version):
    if not version or not re.fullmatch(r"\d+(\.\d+){1,3}", version):
        return None
    return tuple(int(part) for part in version.split("."))


def read_text(path):
    data = path.read_bytes()
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = data[3:].decode("utf-8") if bom else data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MigrationError(f"{path.name} is not UTF-8.") from error
    return text, bom


def walk(root, predicate):
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if predicate(name):
                yield Path(current) / name


# ---------------------------------------------------------------- project files

class Project:
    def __init__(self, path):
        self.path = path
        self.text, self.bom = read_text(path)
        try:
            self.xml = ET.fromstring(self.text)
        except ET.ParseError as error:
            raise MigrationError(f"{path.name} is not valid XML.") from error

    def elements(self, name):
        return [e for e in self.xml.iter() if e.tag == name]

    def framework(self):
        frameworks = self.elements("TargetFramework")
        return (frameworks[0].text or "").strip() if len(frameworks) == 1 else None

    def package_versions(self):
        return {e.get("Include"): e.get("Version") for e in self.elements("PackageReference")}

    def project_references(self):
        return [
            (self.path.parent / e.get("Include").replace("\\", os.sep)).resolve()
            for e in self.elements("ProjectReference") if e.get("Include")
        ]

    def guard(self, blockers):
        name = self.path.name
        if not self.xml.get("Sdk"):
            blockers.append(f"{name}: not an SDK-style project.")
        if self.elements("Import"):
            blockers.append(f"{name}: explicit <Import> requires manual review.")
        if self.elements("TargetFrameworks"):
            blockers.append(f"{name}: multi-targeting (<TargetFrameworks>) requires manual review.")
        if len(self.elements("TargetFramework")) != 1:
            blockers.append(f"{name}: expected exactly one <TargetFramework>.")
        elif self.framework() not in (SOURCE_TFM, TARGET_TFM):
            blockers.append(f"{name}: targets an unreviewed framework.")
        for element in self.xml.iter():
            if element.get("Condition") and element.tag in (
                    "PropertyGroup", "ItemGroup", "TargetFramework", "PackageReference"):
                blockers.append(f"{name}: conditional <{element.tag}> requires manual review.")
                break
        for reference in self.elements("PackageReference"):
            if reference.get("VersionOverride") or reference.find("Version") is not None:
                blockers.append(f"{name}: non-attribute package version requires manual review.")
                break

    def set_framework(self, text):
        return re.sub(r"(<TargetFramework>\s*)net8\.0(\s*</TargetFramework>)",
                      rf"\g<1>{TARGET_TFM}\g<2>", text, count=1)


def package_tag(text, package):
    for match in re.finditer(r"<PackageReference\b[^>]*>", text, re.S):
        if re.search(rf'\bInclude\s*=\s*"{re.escape(package)}"', match.group()):
            return match
    return None


def set_package_version(text, package, version):
    match = package_tag(text, package)
    tag = re.sub(r'(\bVersion\s*=\s*")[^"]*(")', rf"\g<1>{version}\g<2>", match.group(), count=1)
    return text[:match.start()] + tag + text[match.end():]


def add_package_after(text, anchor, package, version):
    match = package_tag(text, anchor)
    if not match or not match.group().rstrip().endswith("/>"):
        raise MigrationError(f"Cannot place {package} next to a non-self-closing reference.")
    line_start = text.rfind("\n", 0, match.start()) + 1
    indent = text[line_start:match.start()]
    newline = "\r\n" if "\r\n" in text else "\n"
    closing = " />" if match.group().rstrip().endswith(" />") else "/>"
    line = f'{newline}{indent}<PackageReference Include="{package}" Version="{version}"{closing}'
    return text[:match.end()] + line + text[match.end():]


def set_project_version(text, version):
    if re.search(r"<Version>[^<]*</Version>", text):
        return re.sub(r"(<Version>)[^<]*(</Version>)", rf"\g<1>{version}\g<2>", text, count=1)
    match = re.search(r"([ \t]*)<TargetFramework>[^<]*</TargetFramework>", text)
    newline = "\r\n" if "\r\n" in text else "\n"
    insertion = f"{newline}{match.group(1)}<Version>{version}</Version>"
    return text[:match.end()] + insertion + text[match.end():]


# ---------------------------------------------------------------- plan

class Plan:
    def __init__(self, root):
        self.root = root
        self.changes = {}   # path -> [original, new, bom, descriptions]
        self.blockers, self.warnings, self.manual = [], [], []

    def rel(self, path):
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def current(self, path, original):
        return self.changes[path][1] if path in self.changes else original

    def stage(self, path, original, new, bom, description):
        if new == self.current(path, original):
            return
        entry = self.changes.setdefault(path, [original, original, bom, []])
        entry[1] = new
        entry[3].append(description)


def find_host_projects(root):
    hosts = []
    for path in walk(root, lambda n: n.endswith(".csproj")):
        if (path.parent / "host.json").exists():
            project = Project(path)
            if WORKFLOWS_PACKAGE in project.package_versions():
                hosts.append(project)
    return hosts


def plan_project(plan, project, role, connector_version=None):
    project.guard(plan.blockers)
    original, path, bom = project.text, project.path, project.bom

    def stage(new, description):
        plan.stage(path, original, new, bom, f"{role}: {description}")

    if project.framework() == SOURCE_TFM:
        stage(project.set_framework(plan.current(path, original)), f"TargetFramework -> {TARGET_TFM}")
    versions = project.package_versions()
    targets = {WORKFLOWS_PACKAGE: REVIEWED[WORKFLOWS_PACKAGE]}
    if role == "Logic App host":
        targets[FUNCTIONS_SDK_PACKAGE] = REVIEWED[FUNCTIONS_SDK_PACKAGE]
    for package, reviewed in targets.items():
        if package not in versions:
            plan.blockers.append(f"{path.name}: missing {package} reference.")
            continue
        current = version_tuple(versions[package])
        if current is None:
            plan.blockers.append(f"{path.name}: {package} has a non-numeric or floating version.")
        elif current < version_tuple(reviewed):
            stage(set_package_version(plan.current(path, original), package, reviewed), f"{package} -> {reviewed}")
        elif current > version_tuple(reviewed):
            plan.warnings.append(f"{path.name}: {package} {versions[package]} is newer than the reviewed {reviewed}; preserved, validate it.")
    if role != "Connector":
        return
    # Reversed so that inserting each pin directly after the anchor keeps declaration order.
    for package, pin in reversed(CONNECTOR_PINS.items()):
        current = versions.get(package)
        if current is None:
            stage(add_package_after(plan.current(path, original), WORKFLOWS_PACKAGE, package, pin),
                  f"add {package} {pin} (NU1605 fix)")
        elif version_tuple(current) is None:
            plan.blockers.append(f"{path.name}: {package} has a non-numeric version.")
        elif version_tuple(current) < version_tuple(pin):
            stage(set_package_version(plan.current(path, original), package, pin), f"{package} -> {pin} (NU1605 fix)")
    if connector_version:
        stage(set_project_version(plan.current(path, original), connector_version), f"Version -> {connector_version}")


def plan_json(plan, path, operations_builder):
    text, bom = read_text(path)
    try:
        for operation, json_path, value, description in operations_builder(Jsonc(text)):
            new = edit_json(plan.current(path, text), operation, json_path, value)
            plan.stage(path, text, new, bom, description)
    except MigrationError as error:
        plan.blockers.append(f"{plan.rel(path)}: {error}")


def sdk_operations(document):
    version = document.get(["sdk", "version"])
    if version is None:
        return []
    current = version_tuple(version)
    if current is None:
        raise MigrationError("sdk.version is not a numeric version.")
    if current < version_tuple(SDK_VERSION):
        return [("set", ["sdk", "version"], SDK_VERSION, f"global.json: sdk.version -> {SDK_VERSION}")]
    return []


def host_operations(document):
    if document.get(["extensionBundle"]) is not None:
        return [("remove", ["extensionBundle"], None, "host.json: remove extensionBundle")]
    return []


def local_operations(document):
    if document.get(["IsEncrypted"]) is True:
        raise MigrationError("Encrypted local settings require manual editing.")
    if not isinstance(document.get(["Values"]), dict):
        raise MigrationError("Missing Values object.")
    runtime = document.get(["Values", "FUNCTIONS_WORKER_RUNTIME"])
    if runtime is None:
        raise MigrationError("FUNCTIONS_WORKER_RUNTIME is missing; add it manually.")
    operations = []
    if runtime != "dotnet-isolated":
        operations.append(("set", ["Values", "FUNCTIONS_WORKER_RUNTIME"], "dotnet-isolated",
                           "local.settings.json: FUNCTIONS_WORKER_RUNTIME -> dotnet-isolated (local only)"))
    for key in BUNDLE_KEYS:
        if document.get(["Values", key]) is not None:
            operations.append(("remove", ["Values", key], None, f"local.settings.json: remove {key}"))
    return operations


def cloud_operations(document):
    runtime = document.get(["Values", "FUNCTIONS_WORKER_RUNTIME"])
    operations = []
    if runtime == "dotnet-isolated":
        operations.append(("set", ["Values", "FUNCTIONS_WORKER_RUNTIME"], "dotnet",
                           "cloud.settings.json: FUNCTIONS_WORKER_RUNTIME -> dotnet (Azure value)"))
    elif runtime not in (None, "dotnet"):
        raise MigrationError("FUNCTIONS_WORKER_RUNTIME has an unexpected value; review manually.")
    for key in BUNDLE_KEYS + ("LOGICAPP_INPROC_REDIRECT",):
        if document.get(["Values", key]) is not None:
            operations.append(("remove", ["Values", key], None, f"cloud.settings.json: remove {key}"))
    return operations


def select_host(root, project_path):
    if project_path:
        host = Project(project_path.resolve())
        if WORKFLOWS_PACKAGE not in host.package_versions() or not (host.path.parent / "host.json").exists():
            raise MigrationError("Selected project is not a NuGet-based Logic App host (needs host.json and the Workflows package).")
        return host
    hosts = find_host_projects(root)
    if not hosts:
        raise MigrationError("No NuGet-based Logic App host found (a .csproj next to host.json referencing the Workflows package).")
    if len(hosts) > 1:
        raise MigrationError("Multiple Logic App hosts found; select one with --project.")
    return hosts[0]


def build_plan(root, project_path=None, connector_version=None):
    plan = Plan(root)
    for name in ("Directory.Build.props", "Directory.Build.targets", "Directory.Packages.props"):
        for path in walk(root, lambda n, name=name: n == name):
            plan.blockers.append(f"{plan.rel(path)}: inherited MSBuild configuration requires manual review.")

    host = select_host(root, project_path)
    if "LogicAppFolderToPublish" in host.text:
        raise MigrationError("This is a custom-code (local function) layout; use logicapps-custom-code-net10-migration.")
    host_dir = host.path.parent
    plan_project(plan, host, "Logic App host")

    connectors = []
    for reference in host.project_references():
        if not reference.exists():
            plan.blockers.append(f"Referenced project {plan.rel(reference)} not found.")
            continue
        project = Project(reference)
        if WORKFLOWS_PACKAGE in project.package_versions():
            connectors.append(project)
        else:
            plan.manual.append(f"{plan.rel(reference)}: referenced project without the Workflows package; review its framework manually.")
    if not connectors:
        plan.warnings.append("No connector project referenced by the host; only host configuration is migrated.")
    for project in connectors:
        plan_project(plan, project, "Connector", connector_version)

    connector_paths = {p.path for p in connectors}
    for path in walk(root, lambda n: n.endswith(".csproj")):
        if path == host.path or path in connector_paths:
            continue
        project = Project(path)
        if connector_paths & set(project.project_references()):
            project.guard(plan.blockers)
            if project.framework() == SOURCE_TFM:
                plan.stage(path, project.text, project.set_framework(project.text), project.bom,
                           f"Test project: TargetFramework -> {TARGET_TFM}")

    for global_json in sorted({root / "global.json", host_dir / "global.json"}):
        if global_json.exists():
            plan_json(plan, global_json, sdk_operations)

    if (host_dir / "host.json").exists():
        plan_json(plan, host_dir / "host.json", host_operations)
    if (host_dir / "local.settings.json").exists():
        plan_json(plan, host_dir / "local.settings.json", local_operations)
    else:
        plan.manual.append(f"{plan.rel(host_dir / 'local.settings.json')} not found; set FUNCTIONS_WORKER_RUNTIME=dotnet-isolated when you create it.")
    if (host_dir / "cloud.settings.json").exists():
        plan_json(plan, host_dir / "cloud.settings.json", cloud_operations)

    for vscode in sorted({root / ".vscode", host_dir / ".vscode"}):
        for name in ("settings.json", "tasks.json", "launch.json"):
            path = vscode / name
            if path.exists():
                text, bom = read_text(path)
                plan.stage(path, text, BUILD_PATH.sub(rf"\g<1>{TARGET_TFM}", text), bom,
                           f".vscode/{name}: build output paths -> {TARGET_TFM}")

    for path in walk(root, lambda n: Path(n).suffix.lower() in SCRIPT_SUFFIXES or n.endswith(".code-workspace")):
        if path not in plan.changes and SOURCE_TFM in read_text(path)[0]:
            plan.manual.append(f"{plan.rel(path)} references {SOURCE_TFM}; review and update manually.")
    return plan


# ---------------------------------------------------------------- apply and report

def encode(text, bom):
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def apply(plan):
    written = []
    try:
        for path, (original, new, bom, _) in plan.changes.items():
            if read_text(path)[0] != original:
                raise MigrationError(f"{plan.rel(path)} changed during migration; rolled back.")
            handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(encode(new, bom))
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.remove(temp)
            written.append((path, original, bom))
    except Exception:
        for path, original, bom in reversed(written):
            path.write_bytes(encode(original, bom))
        raise


AZURE_PREREQUISITES = [
    "Delete app settings AzureFunctionsJobHost__extensionBundle__id and AzureFunctionsJobHost__extensionBundle__version (they override host.json).",
    "Delete LOGICAPP_INPROC_REDIRECT if present (it forces the legacy .NET 8 host).",
    "Keep FUNCTIONS_WORKER_RUNTIME=dotnet, FUNCTIONS_EXTENSION_VERSION=~4, and APP_KIND=workflowApp; netFrameworkVersion needs no change.",
    "Deploy bin\\Release\\net10.0\\publish. For an existing app, stop it, change settings, deploy, then start.",
]


def report(plan, applied):
    print(("Applied" if applied else "Dry run (no files written)") + f": {plan.root}")
    for path, (_, _, _, descriptions) in plan.changes.items():
        print(f"\n  {plan.rel(path)}")
        for description in descriptions:
            print(f"    - {description}")
    if not plan.changes:
        print("\n  No configuration changes needed.")
    for title, items in (("Blockers", plan.blockers), ("Warnings", plan.warnings), ("Manual follow-up", plan.manual)):
        if items:
            print(f"\n{title}:")
            for item in items:
                print(f"  - {item}")
    print("\nLocal prerequisite: Core Tools 4.16.0 or later (main host on .NET 10) must be the func on PATH.")
    print("Azure prerequisites (report only, not performed):")
    for item in AZURE_PREREQUISITES:
        print(f"  - {item}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Solution root.")
    parser.add_argument("--project", type=Path, help="Logic App host .csproj when several exist.")
    parser.add_argument("--connector-version", help="Set <Version> on connector projects, for example 2.0.0.")
    parser.add_argument("--apply", action="store_true", help="Write the planned changes.")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print("Blocked: root directory not found.", file=sys.stderr)
        return 1
    if args.connector_version and version_tuple(args.connector_version) is None:
        print("Blocked: --connector-version must be numeric, for example 2.0.0.", file=sys.stderr)
        return 1
    try:
        plan = build_plan(root, args.project, args.connector_version)
        if plan.blockers:
            report(plan, False)
            return 1
        if args.apply:
            apply(plan)
        report(plan, args.apply)
        return 0
    except MigrationError as error:
        print(f"Blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
