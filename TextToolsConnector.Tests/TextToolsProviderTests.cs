using System.Globalization;
using System.Net;
using System.Reflection;
using Microsoft.Azure.WebJobs;
using Microsoft.Azure.WebJobs.Host.Config;
using Microsoft.Azure.WebJobs.Hosting;
using Microsoft.Azure.Workflows.ServiceProviders.Abstractions;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.WindowsAzure.ResourceStack.Common.Collections;
using Microsoft.WindowsAzure.ResourceStack.Common.Json;
using Microsoft.WindowsAzure.ResourceStack.Common.Swagger.Entities;
using Newtonsoft.Json.Linq;
using TextToolsConnector;

namespace TextToolsConnector.Tests;

public sealed class TextToolsProviderTests
{
    private readonly TextToolsServiceOperationsProvider operations = new();

    [Theory]
    [InlineData("  Hello Logic Apps  ", "Hello Logic Apps", "HELLO LOGIC APPS", 16)]
    [InlineData("\t a \t b \r\n", "a \t b", "A \t B", 5)]
    [InlineData("\u00a0a 🙂\u00a0", "a 🙂", "A 🙂", 4)]
    public async Task AnalyzeTextReturnsTrimmedInvariantOutput(
        string input,
        string normalized,
        string uppercase,
        int characterCount)
    {
        using var response = await operations.InvokeOperation(
            TextToolsServiceOperationsProvider.AnalyzeTextOperationId,
            Connection(),
            Request(new JValue(input)));

        Assert.Equal(HttpStatusCode.OK, response.HttpStatus);
        var body = Assert.IsType<JObject>(response.Body);
        Assert.Equal(normalized, body.Value<string>("normalizedText"));
        Assert.Equal(uppercase, body.Value<string>("uppercaseText"));
        Assert.Equal(characterCount, body.Value<int>("characterCount"));
        Assert.Equal(3, body.Count);
    }

    [Fact]
    public async Task AnalyzeTextUsesInvariantCulture()
    {
        var originalCulture = CultureInfo.CurrentCulture;
        try
        {
            CultureInfo.CurrentCulture = CultureInfo.GetCultureInfo("tr-TR");

            using var response = await operations.InvokeOperation(
                TextToolsServiceOperationsProvider.AnalyzeTextOperationId,
                Connection(),
                Request(new JValue("  izmir  ")));

            Assert.Equal(HttpStatusCode.OK, response.HttpStatus);
            Assert.Equal("IZMIR", response.Body.Value<string>("uppercaseText"));
        }
        finally
        {
            CultureInfo.CurrentCulture = originalCulture;
        }
    }

    [Theory]
    [InlineData("null")]
    [InlineData("42")]
    [InlineData("true")]
    [InlineData("{}")]
    [InlineData("[]")]
    [InlineData("\"\"")]
    [InlineData("\" \\t\\r\\n \"")]
    [InlineData("\"\\u00a0\"")]
    public async Task AnalyzeTextRejectsInvalidJsonValues(string json)
    {
        await AssertInvalidText(Request(JToken.Parse(json)));
    }

    [Fact]
    public async Task AnalyzeTextRejectsMissingInputAndRequest()
    {
        await AssertInvalidText(new TestRequest { Parameters = new InsensitiveDictionary<JToken>() });
        await AssertInvalidText(new TestRequest { Parameters = null! });
        await AssertInvalidText(new TestRequest
        {
            Parameters = new InsensitiveDictionary<JToken> { ["text"] = null! },
        });
        await AssertInvalidText(null);
    }

    [Theory]
    [InlineData("3")]
    [InlineData("\"3\"")]
    public async Task ConnectionLimitCountsInputUtf16UnitsBeforeTrimming(string limitJson)
    {
        var connection = new InsensitiveDictionary<JToken>
        {
            ["maxTextLength"] = JToken.Parse(limitJson),
        };
        using var accepted = await operations.InvokeOperation(
            TextToolsServiceOperationsProvider.AnalyzeTextOperationId,
            connection,
            Request(new JValue("a\U0001F600")));
        Assert.Equal(HttpStatusCode.OK, accepted.HttpStatus);

        foreach (var text in new[] { "a\U0001F600x", " ab " })
        {
            using var rejected = await operations.InvokeOperation(
                TextToolsServiceOperationsProvider.AnalyzeTextOperationId,
                connection,
                Request(new JValue(text)));
            Assert.Equal(HttpStatusCode.BadRequest, rejected.HttpStatus);
            Assert.Equal("TextTooLong", rejected.Body["error"]?.Value<string>("code"));
        }
    }

    [Theory]
    [InlineData(null)]
    [InlineData("null")]
    [InlineData("0")]
    [InlineData("-1")]
    [InlineData("2147483648")]
    [InlineData("1.5")]
    [InlineData("true")]
    [InlineData("\"invalid\"")]
    public async Task InvalidConnectionLimitFailsExplicitly(string? limitJson)
    {
        var connection = new InsensitiveDictionary<JToken>();
        if (limitJson is not null)
        {
            connection["maxTextLength"] = JToken.Parse(limitJson);
        }

        await Assert.ThrowsAsync<InvalidOperationException>(() => operations.InvokeOperation(
            TextToolsServiceOperationsProvider.AnalyzeTextOperationId,
            connection,
            Request(new JValue("text"))));
    }

    [Theory]
    [InlineData("other")]
    [InlineData("analyzeTextExtra")]
    [InlineData("")]
    [InlineData(null)]
    public async Task UnsupportedOperationIdsFail(string? operationId)
    {
        await Assert.ThrowsAsync<NotSupportedException>(() => operations.InvokeOperation(
            operationId!,
            new InsensitiveDictionary<JToken>(),
            Request(new JValue("text"))));
    }

    [Fact]
    public void ActionMetadataDescribesRequiredInputAndOutput()
    {
        var service = operations.GetService();
        Assert.Equal(TextToolsServiceOperationsProvider.ServiceId, service.Id);
        Assert.Equal(DesignerApiType.ServiceProvider, service.Type);
        Assert.Equal(new[] { ApiCapability.Actions }, service.Properties.Capabilities);
        var connectionParameters = Assert.IsType<ConnectionParameters>(service.Properties.ConnectionParameters);
        Assert.Null(connectionParameters.ConnectionString);
        var limit = Assert.Single(connectionParameters.AdditionalParameters);
        Assert.Equal("maxTextLength", limit.Key);
        Assert.Equal(ConnectionStringType.IntegerType, limit.Value.Type);
        Assert.Equal("true", limit.Value.UIDefinition.Constraints.Required);

        var unexpanded = Assert.Single(operations.GetOperations(expandManifest: false));
        Assert.Equal(TextToolsServiceOperationsProvider.AnalyzeTextOperationId, unexpanded.Id);
        Assert.Equal(OperationType.ServiceProvider, unexpanded.Properties.OperationType);
        Assert.Null(unexpanded.Properties.Manifest);

        var expanded = Assert.Single(operations.GetOperations(expandManifest: true));
        var manifest = Assert.IsType<ServiceOperationManifest>(expanded.Properties.Manifest);
        Assert.Equal(ConnectionReferenceKeyFormat.ServiceProvider, manifest.ConnectionReference.ReferenceKeyFormat);
        Assert.Equal(new[] { InputsLocation.Inputs, InputsLocation.Parameters }, manifest.InputsLocation);
        Assert.Equal(new[] { "text" }, manifest.Inputs.Required);
        Assert.Equal(SwaggerSchemaType.String, manifest.Inputs.Properties["text"].Type);
        Assert.Equal(1L, manifest.Inputs.Properties["text"].MinLength);

        var body = manifest.Outputs.Properties["body"];
        Assert.Equal(SwaggerSchemaType.Object, body.Type);
        Assert.Equal(new[] { "normalizedText", "uppercaseText", "characterCount" }, body.Required);
        Assert.Equal(SwaggerSchemaType.String, body.Properties["normalizedText"].Type);
        Assert.Equal(SwaggerSchemaType.String, body.Properties["uppercaseText"].Type);
        Assert.Equal(SwaggerSchemaType.Integer, body.Properties["characterCount"].Type);

        var serializedService = service.ToJToken();
        Assert.Equal("ServiceProvider", serializedService["type"]?.Value<string>());
        Assert.Equal("actions", serializedService["properties"]?["capabilities"]?[0]?.Value<string>());
        Assert.Equal("int", serializedService["properties"]?["connectionParameters"]?["additionalParameters"]?["maxTextLength"]?["type"]?.Value<string>());
        var serializedManifest = expanded.ToJToken()["properties"]?["manifest"];
        Assert.Equal("string", serializedManifest?["inputs"]?["properties"]?["text"]?["type"]?.Value<string>());
        Assert.Equal("integer", serializedManifest?["outputs"]?["properties"]?["body"]?["properties"]?["characterCount"]?["type"]?.Value<string>());
    }

    [Fact]
    public async Task StartupRegistersDiscoverableProviderAndDispatchesItsAction()
    {
        Assert.Contains(
            typeof(TextToolsStartup).Assembly.GetCustomAttributes<WebJobsStartupAttribute>(),
            attribute => attribute.WebJobsStartupType == typeof(TextToolsStartup));

        var services = new ServiceCollection();
        services.AddSingleton<ServiceOperationsProvider>();
        new TextToolsStartup().Configure(new TestWebJobsBuilder(services));

        using var container = services.BuildServiceProvider();
        Assert.Single(container.GetServices<IExtensionConfigProvider>().OfType<TextToolsExtension>());

        var registry = container.GetRequiredService<ServiceOperationsProvider>();
        Assert.True(registry.ContainsService(TextToolsServiceOperationsProvider.ServiceName));
        Assert.True(registry.ContainsServiceOperation(
            TextToolsServiceOperationsProvider.ServiceName,
            TextToolsServiceOperationsProvider.AnalyzeTextOperationId));
        var operation = registry.GetServiceOperationUsingOperationId(
            TextToolsServiceOperationsProvider.ServiceId,
            TextToolsServiceOperationsProvider.AnalyzeTextOperationId,
            expandManifest: true);
        Assert.Equal("text", Assert.Single(operation.Properties.Manifest.Inputs.Required));
        var registered = registry.LoadServiceOperationsProvider(TextToolsServiceOperationsProvider.ServiceId);
        Assert.IsType<TextToolsServiceOperationsProvider>(registered);

        using var response = await registered.InvokeOperation(
            TextToolsServiceOperationsProvider.AnalyzeTextOperationId,
            Connection(),
            Request(new JValue("  registry  ")));
        Assert.Equal(HttpStatusCode.OK, response.HttpStatus);
        Assert.Equal("REGISTRY", response.Body.Value<string>("uppercaseText"));
    }

    [Fact]
    public void ActionOnlyProviderDoesNotExposeTriggerBinding()
    {
        Assert.Throws<NotSupportedException>(() => operations.GetBindingConnectionInformation(
            TextToolsServiceOperationsProvider.AnalyzeTextOperationId,
            new InsensitiveDictionary<JToken>()));
    }

    private async Task AssertInvalidText(ServiceOperationRequest? request)
    {
        using var response = await operations.InvokeOperation(
            TextToolsServiceOperationsProvider.AnalyzeTextOperationId,
            Connection(),
            request!);

        Assert.Equal(HttpStatusCode.BadRequest, response.HttpStatus);
        var body = Assert.IsType<JObject>(response.Body);
        Assert.Equal("InvalidText", body["error"]?.Value<string>("code"));
        Assert.False(string.IsNullOrWhiteSpace(body["error"]?.Value<string>("message")));
    }

    private static TestRequest Request(JToken text) => new()
    {
        Parameters = new InsensitiveDictionary<JToken>
        {
            ["text"] = text,
        },
    };

    private static InsensitiveDictionary<JToken> Connection() => new()
    {
        ["maxTextLength"] = new JValue(100000),
    };

    private sealed class TestRequest : ServiceOperationRequest
    {
        public override Task<ServiceProviderAuthenticationToken> GetAuthenticationToken(string authenticationIdentifier) =>
            throw new InvalidOperationException("TextTools does not use authentication.");
    }

    private sealed class TestWebJobsBuilder(IServiceCollection services) : IWebJobsBuilder
    {
        public IServiceCollection Services { get; } = services;
    }
}
