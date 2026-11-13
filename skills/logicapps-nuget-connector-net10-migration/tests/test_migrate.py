import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import migrate  # noqa: E402

HOST_CSPROJ = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <AzureFunctionsVersion>v4</AzureFunctionsVersion>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Sdk.Functions" Version="4.4.0"/>
  </ItemGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.Azure.Workflows.WebJobs.Extension" Version="1.165.50"/>
    <ProjectReference Include="..\\Connector\\Connector.csproj"/>
  </ItemGroup>
</Project>
"""

CONNECTOR_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.Azure.Workflows.WebJobs.Extension" Version="1.165.50" />
  </ItemGroup>
</Project>
"""

TESTS_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <ProjectReference Include="..\\Connector\\Connector.csproj" />
  </ItemGroup>
</Project>
"""

HOST_JSON = """{
  "version": "2.0",
  "logging": { "logLevel": { "default": "Information" } },
  "extensionBundle": {
    "id": "Microsoft.Azure.Functions.ExtensionBundle.Workflows",
    "version": "[1.*, 2.0.0)"
  }
}
"""

SECRET = "AccountKey=do-not-print"
LOCAL_SETTINGS = json.dumps({
    "IsEncrypted": False,
    "Values": {
        "AzureWebJobsStorage": SECRET,
        "FUNCTIONS_WORKER_RUNTIME": "dotnet",
        "AzureFunctionsJobHost__extensionBundle__id": "Microsoft.Azure.Functions.ExtensionBundle.Workflows",
    },
}, indent=2)

CLOUD_SETTINGS = json.dumps({
    "IsEncrypted": False,
    "Values": {
        "FUNCTIONS_WORKER_RUNTIME": "dotnet-isolated",
        "LOGICAPP_INPROC_REDIRECT": "1",
        "APP_KIND": "workflowapp",
    },
}, indent=2)

TASKS_JSON = """{
  // build output
  "tasks": [ { "options": { "cwd": "bin\\\\Debug\\\\net8.0" } },
             { "options": { "cwd": "${workspaceFolder}/bin/Debug/net8.0" } } ]
}
"""


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        files = {
            "global.json": '{ "sdk": { "version": "8.0.400", "rollForward": "latestPatch" } }',
            "LogicApp/LogicApp.csproj": HOST_CSPROJ,
            "LogicApp/host.json": HOST_JSON,
            "LogicApp/local.settings.json": LOCAL_SETTINGS,
            "LogicApp/cloud.settings.json": CLOUD_SETTINGS,
            "LogicApp/.vscode/tasks.json": TASKS_JSON,
            "LogicApp/.vscode/settings.json": '{ "azureLogicAppsStandard.deploySubpath": "bin\\\\Release\\\\net8.0\\\\publish" }',
            "Connector/Connector.csproj": CONNECTOR_CSPROJ,
            "Connector.Tests/Connector.Tests.csproj": TESTS_CSPROJ,
            "scripts/run.ps1": "Set-Location ..\\LogicApp\\bin\\Debug\\net8.0",
        }
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (self.root / "Connector/Connector.csproj").write_bytes(
            b"\xef\xbb\xbf" + CONNECTOR_CSPROJ.encode("utf-8"))

    def tearDown(self):
        self.temp.cleanup()

    def run_helper(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = migrate.main(["--root", str(self.root), *args])
        return code, out.getvalue() + err.getvalue()

    def read(self, name):
        return (self.root / name).read_text(encoding="utf-8-sig")

    def snapshot(self):
        return {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_dry_run_writes_nothing_and_hides_values(self):
        before = self.snapshot()
        code, output = self.run_helper()
        self.assertEqual(code, 0)
        self.assertEqual(before, self.snapshot())
        self.assertIn("Dry run", output)
        self.assertNotIn(SECRET, output)
        self.assertIn("scripts", output)

    def test_apply_migrates_projects(self):
        code, _ = self.run_helper("--apply", "--connector-version", "2.0.0")
        self.assertEqual(code, 0)
        host = self.read("LogicApp/LogicApp.csproj")
        self.assertIn("<TargetFramework>net10.0</TargetFramework>", host)
        self.assertIn('Include="Microsoft.NET.Sdk.Functions" Version="4.5.0"/>', host)
        self.assertIn('Version="1.194.31"', host)
        connector = self.read("Connector/Connector.csproj")
        self.assertIn("<Version>2.0.0</Version>", connector)
        abstractions = connector.index("DependencyInjection.Abstractions\" Version=\"10.0.0\" />")
        primitives = connector.index("Primitives\" Version=\"9.0.9\" />")
        self.assertLess(abstractions, primitives)
        self.assertTrue((self.root / "Connector/Connector.csproj").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn("net10.0", self.read("Connector.Tests/Connector.Tests.csproj"))
        self.assertEqual(json.loads(self.read("global.json"))["sdk"]["version"], "10.0.403")

    def test_apply_fixes_settings_and_paths(self):
        self.run_helper("--apply")
        host = json.loads(self.read("LogicApp/host.json"))
        self.assertNotIn("extensionBundle", host)
        self.assertEqual(host["logging"]["logLevel"]["default"], "Information")
        local = json.loads(self.read("LogicApp/local.settings.json"))["Values"]
        self.assertEqual(local["FUNCTIONS_WORKER_RUNTIME"], "dotnet-isolated")
        self.assertEqual(local["AzureWebJobsStorage"], SECRET)
        self.assertNotIn("AzureFunctionsJobHost__extensionBundle__id", local)
        cloud = json.loads(self.read("LogicApp/cloud.settings.json"))["Values"]
        self.assertEqual(cloud["FUNCTIONS_WORKER_RUNTIME"], "dotnet")
        self.assertNotIn("LOGICAPP_INPROC_REDIRECT", cloud)
        tasks = self.read("LogicApp/.vscode/tasks.json")
        self.assertNotIn("net8.0", tasks)
        self.assertIn("// build output", tasks)
        self.assertIn("net10.0", self.read("LogicApp/.vscode/settings.json"))
        self.assertIn("net8.0", self.read("scripts/run.ps1"))

    def test_second_run_is_idempotent(self):
        self.run_helper("--apply", "--connector-version", "2.0.0")
        before = self.snapshot()
        code, output = self.run_helper("--apply", "--connector-version", "2.0.0")
        self.assertEqual(code, 0)
        self.assertIn("No configuration changes needed", output)
        self.assertEqual(before, self.snapshot())

    def test_newer_versions_are_preserved(self):
        path = self.root / "LogicApp/LogicApp.csproj"
        path.write_text(HOST_CSPROJ.replace('Version="4.4.0"', 'Version="4.6.0"'), encoding="utf-8")
        self.run_helper("--apply")
        self.assertIn('Version="4.6.0"', self.read("LogicApp/LogicApp.csproj"))

    def test_blocks_custom_code_layout(self):
        path = self.root / "LogicApp/LogicApp.csproj"
        path.write_text(HOST_CSPROJ.replace("<AzureFunctionsVersion>",
                        "<LogicAppFolderToPublish>x</LogicAppFolderToPublish><AzureFunctionsVersion>"), encoding="utf-8")
        before = self.snapshot()
        code, output = self.run_helper("--apply")
        self.assertEqual(code, 1)
        self.assertIn("logicapps-custom-code-net10-migration", output)
        self.assertEqual(before, self.snapshot())

    def test_blocks_central_package_management(self):
        (self.root / "Directory.Packages.props").write_text("<Project />", encoding="utf-8")
        before = self.snapshot()
        code, output = self.run_helper("--apply")
        self.assertEqual(code, 1)
        self.assertIn("Directory.Packages.props", output)
        self.assertEqual(before, self.snapshot())

    def test_blocks_encrypted_local_settings(self):
        (self.root / "LogicApp/local.settings.json").write_text('{ "IsEncrypted": true, "Values": {} }', encoding="utf-8")
        code, output = self.run_helper("--apply")
        self.assertEqual(code, 1)
        self.assertIn("Encrypted", output)
        self.assertIn("net8.0", self.read("LogicApp/LogicApp.csproj"))

    def test_blocks_conditional_framework(self):
        path = self.root / "Connector/Connector.csproj"
        path.write_text(CONNECTOR_CSPROJ.replace("<PropertyGroup>", "<PropertyGroup Condition=\"'$(X)'==''\">"), encoding="utf-8")
        code, output = self.run_helper("--apply")
        self.assertEqual(code, 1)
        self.assertIn("conditional", output)

    def test_rolls_back_on_concurrent_change(self):
        plan = migrate.build_plan(self.root.resolve())
        paths = list(plan.changes)
        paths[-1].write_text(paths[-1].read_text(encoding="utf-8-sig") + " ", encoding="utf-8")
        first_original = paths[0].read_bytes()
        with self.assertRaises(migrate.MigrationError):
            migrate.apply(plan)
        self.assertEqual(paths[0].read_bytes(), first_original)


if __name__ == "__main__":
    unittest.main()
