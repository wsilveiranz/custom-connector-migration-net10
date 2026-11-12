using Microsoft.Azure.WebJobs;
using Microsoft.Azure.WebJobs.Description;
using Microsoft.Azure.WebJobs.Host.Config;
using Microsoft.Azure.WebJobs.Hosting;
using Microsoft.Azure.Workflows.ServiceProviders.Abstractions;
using Microsoft.Extensions.DependencyInjection.Extensions;

[assembly: WebJobsStartup(typeof(TextToolsConnector.TextToolsStartup))]

namespace TextToolsConnector;

public sealed class TextToolsStartup : IWebJobsStartup
{
    public void Configure(IWebJobsBuilder builder)
    {
        builder.AddExtension<TextToolsExtension>();
        builder.Services.TryAddSingleton<TextToolsServiceOperationsProvider>();
    }
}

[Extension("TextToolsServiceProvider", configurationSection: "TextToolsServiceProvider")]
public sealed class TextToolsExtension : IExtensionConfigProvider
{
    public TextToolsExtension(
        ServiceOperationsProvider serviceOperationsProvider,
        TextToolsServiceOperationsProvider operationsProvider)
    {
        serviceOperationsProvider.RegisterService(
            serviceName: TextToolsServiceOperationsProvider.ServiceName,
            serviceOperationsProviderId: TextToolsServiceOperationsProvider.ServiceId,
            serviceOperationsProviderInstance: operationsProvider);
    }

    public void Initialize(ExtensionConfigContext context)
    {
    }
}
