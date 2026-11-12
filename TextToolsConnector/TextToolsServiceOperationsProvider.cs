using System.Globalization;
using System.Net;
using System.Text;
using Microsoft.Azure.Workflows.ServiceProviders.Abstractions;
using Microsoft.WindowsAzure.ResourceStack.Common.Collections;
using Microsoft.WindowsAzure.ResourceStack.Common.Swagger.Entities;
using Newtonsoft.Json.Linq;

namespace TextToolsConnector;

[ServiceOperationsProvider(Id = ServiceId, Name = ServiceName)]
public sealed class TextToolsServiceOperationsProvider : IServiceOperationsProvider
{
    public const string ServiceName = "textTools";
    public const string ServiceId = "/serviceProviders/textTools";
    public const string AnalyzeTextOperationId = "analyzeText";

    private const uint BrandColor = 0x1767A7;
    private static readonly Uri IconUri = new(
        "data:image/svg+xml;base64," +
        Convert.ToBase64String(Encoding.UTF8.GetBytes(
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='white' d='M3 4h18v3H3zM10 8h4v12h-4z'/></svg>")));

    public ServiceOperationApi GetService() => new()
    {
        Name = ServiceName,
        Id = ServiceId,
        Type = DesignerApiType.ServiceProvider,
        Properties = new ServiceOperationApiProperties
        {
            DisplayName = "TextTools",
            Description = "Analyze text without an external service or credentials.",
            BrandColor = BrandColor,
            IconUri = IconUri,
            Capabilities = new[] { ApiCapability.Actions },
            ConnectionParameters = new ConnectionParameters
            {
                AdditionalParameters = new InsensitiveDictionary<ConnectionStringParameters>
                {
                    ["maxTextLength"] = new ConnectionStringParameters
                    {
                        Type = ConnectionStringType.IntegerType,
                        UIDefinition = new UIDefinition
                        {
                            DisplayName = "Maximum text length",
                            Description = "Maximum input length in UTF-16 code units, before trimming.",
                            Tooltip = "Enter a positive integer.",
                            Constraints = new Constraints { Required = "true", Default = "100000" },
                        },
                    },
                },
            },
        },
    };

    public IEnumerable<ServiceOperation> GetOperations(bool expandManifest)
    {
        var service = GetService();
        var operation = new ServiceOperation
        {
            Name = AnalyzeTextOperationId,
            Id = AnalyzeTextOperationId,
            Type = AnalyzeTextOperationId,
            Properties = new ServiceOperationProperties
            {
                Api = service.GetFlattenedApi(),
                Summary = "Analyze text",
                Description = "Trim text, convert it to invariant uppercase, and count UTF-16 code units.",
                Visibility = Visibility.Important,
                OperationType = OperationType.ServiceProvider,
                BrandColor = BrandColor,
                IconUri = IconUri,
            },
        };

        return new[]
        {
            expandManifest ? operation.CloneWithManifest(CreateManifest(service)) : operation,
        };
    }

    public string GetBindingConnectionInformation(
        string operationId,
        InsensitiveDictionary<JToken> connectionParameters) =>
        throw new NotSupportedException("TextTools does not provide function triggers.");

    public Task<ServiceOperationResponse> InvokeOperation(
        string operationId,
        InsensitiveDictionary<JToken> connectionParameters,
        ServiceOperationRequest serviceOperationRequest)
    {
        if (!string.Equals(operationId, AnalyzeTextOperationId, StringComparison.OrdinalIgnoreCase))
        {
            throw new NotSupportedException($"Unsupported TextTools operation '{operationId}'.");
        }

        if (connectionParameters is null ||
            !connectionParameters.TryGetValue("maxTextLength", out var limit) ||
            limit is null ||
            limit.Type is not (JTokenType.Integer or JTokenType.String) ||
            !int.TryParse(limit.ToString(), NumberStyles.None, CultureInfo.InvariantCulture, out var maxTextLength) ||
            maxTextLength <= 0)
        {
            throw new InvalidOperationException(
                "The TextTools connection requires 'maxTextLength' to be a positive 32-bit integer.");
        }

        var parameters = serviceOperationRequest?.Parameters;
        if (parameters is null ||
            !parameters.TryGetValue("text", out var value) ||
            value is null ||
            value.Type != JTokenType.String)
        {
            return InvalidTextResponse();
        }

        var text = value.Value<string>();
        if (string.IsNullOrWhiteSpace(text))
        {
            return InvalidTextResponse();
        }

        if (text.Length > maxTextLength)
        {
            return Task.FromResult(new ServiceOperationResponse(
                new JObject
                {
                    ["error"] = new JObject
                    {
                        ["code"] = "TextTooLong",
                        ["message"] = $"The 'text' parameter must not exceed {maxTextLength} UTF-16 code units.",
                    },
                },
                HttpStatusCode.BadRequest));
        }

        var normalizedText = text.Trim();
        var body = new JObject
        {
            ["normalizedText"] = normalizedText,
            ["uppercaseText"] = normalizedText.ToUpperInvariant(),
            ["characterCount"] = normalizedText.Length,
        };

        return Task.FromResult(new ServiceOperationResponse(body, HttpStatusCode.OK));
    }

    private static Task<ServiceOperationResponse> InvalidTextResponse() =>
        Task.FromResult(new ServiceOperationResponse(
            new JObject
            {
                ["error"] = new JObject
                {
                    ["code"] = "InvalidText",
                    ["message"] = "The 'text' parameter must be a non-empty string containing non-whitespace characters.",
                },
            },
            HttpStatusCode.BadRequest));

    private static ServiceOperationManifest CreateManifest(ServiceOperationApi service) => new()
    {
        ConnectionReference = new ConnectionReferenceFormat
        {
            ReferenceKeyFormat = ConnectionReferenceKeyFormat.ServiceProvider,
        },
        Settings = new OperationManifestSettings
        {
            SecureData = new OperationManifestSettingWithOptions<SecureDataOptions>(),
            TrackedProperties = new OperationManifestSetting
            {
                Scopes = new[] { OperationScope.Action },
            },
        },
        InputsLocation = new[] { InputsLocation.Inputs, InputsLocation.Parameters },
        Inputs = new SwaggerSchema
        {
            Type = SwaggerSchemaType.Object,
            Properties = new OrdinalDictionary<SwaggerSchema>
            {
                ["text"] = new SwaggerSchema
                {
                    Type = SwaggerSchemaType.String,
                    Title = "Text",
                    Description = "Text containing at least one non-whitespace character.",
                    MinLength = 1,
                },
            },
            Required = new[] { "text" },
        },
        Outputs = new SwaggerSchema
        {
            Type = SwaggerSchemaType.Object,
            Properties = new OrdinalDictionary<SwaggerSchema>
            {
                ["body"] = new SwaggerSchema
                {
                    Type = SwaggerSchemaType.Object,
                    Properties = new OrdinalDictionary<SwaggerSchema>
                    {
                        ["normalizedText"] = new SwaggerSchema
                        {
                            Type = SwaggerSchemaType.String,
                            Title = "Normalized text",
                            Description = "Text with leading and trailing whitespace removed.",
                        },
                        ["uppercaseText"] = new SwaggerSchema
                        {
                            Type = SwaggerSchemaType.String,
                            Title = "Uppercase text",
                            Description = "Normalized text converted to uppercase using the invariant culture.",
                        },
                        ["characterCount"] = new SwaggerSchema
                        {
                            Type = SwaggerSchemaType.Integer,
                            Format = "int32",
                            Title = "Character count",
                            Description = "The normalized text length in UTF-16 code units.",
                        },
                    },
                    Required = new[] { "normalizedText", "uppercaseText", "characterCount" },
                },
            },
        },
        Connector = service,
    };
}
