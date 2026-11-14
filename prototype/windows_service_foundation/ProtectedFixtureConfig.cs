using System.Text.RegularExpressions;

internal sealed record ProtectedFixtureConfig(string PipeName, string BrokerSid, string ObserverSid)
{
    internal const string Mode = "service-protected-fixture";
    internal const string Approval = "--approved-vm-fixture";
    internal const string ServiceName = "SovereignDevBrokerFoundation";
    internal const uint ObserverRights = 0x00120081;
    internal const uint OwnerRights = 0x0012019F;

    internal static ProtectedFixtureConfig? Parse(string[] args) =>
        args is [Mode, Approval, var pipe, var broker, var observer] &&
        Regex.IsMatch(pipe, @"\ASovereignAI-identity-dev-[a-f0-9]{32}\z") &&
        AccountSid(broker) && AccountSid(observer) && broker != observer
            ? new(pipe, broker, observer) : null;

    static bool AccountSid(string value) => value.Length <= 80 &&
        Regex.IsMatch(value, @"\AS-1-5-21-(?:[0-9]+-){3}[0-9]+\z") &&
        value.Split('-').Skip(4).All(part => uint.TryParse(part, out var number) &&
            number.ToString(System.Globalization.CultureInfo.InvariantCulture) == part);

    internal string Command(string executable) => $"\"{executable}\" {Mode} {Approval} {PipeName} {BrokerSid} {ObserverSid}";
    internal string Descriptor(string serviceSid) =>
        $"O:{BrokerSid}D:P(A;;0x{OwnerRights:X8};;;{BrokerSid})(A;;0x{OwnerRights:X8};;;{serviceSid})(A;;0x{ObserverRights:X8};;;{ObserverSid})";
}
