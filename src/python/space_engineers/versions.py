import re
import requests

class Logger:
    def info(self, msg, **kwargs):
        print(" INFO: " + msg)

    def warn(self, msg, **kwargs):
        print(" WARN: " + msg)

    def error(self, msg, **kwargs):
        print("ERROR: " + msg)

class OperatorLogger(Logger):
    def __init__(self, operator):
        self.operator = operator

    def info(self, msg, **kwargs):
        self.report({'INFO'}, msg)

    def warn(self, msg, **kwargs):
        self.report({'WARNING'}, msg)

    def error(self, msg, **kwargs):
        self.report({'ERROR'}, msg)

_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:(-)(\w+)?)?(?:\+(\w+))?")

class Version:
    def __init__(self, version=(0,0,0), prerelease=False, qualifier=None, weburl=None):
        if isinstance(version, str):
            match = _VERSION_PATTERN.match(version)
            if not match:
                raise ValueError("%s is no version" % version)
            self.major = int(match.group(1))
            self.minor = int(match.group(2))
            self.micro = int(match.group(3))
            self.qualifier = qualifier or match.group(5) or match.group(6)
            self.prerelease = prerelease == True or '-' == match.group(4)
            self.weburl = weburl
        else:
            self.major, self.minor, self.micro = version
            self.qualifier = qualifier
            self.prerelease = prerelease == True
            self.weburl = weburl

    def __lt__(self, other):
        if other is None: return False

        if self.major != other.major:
            return self.major < other.major
        if self.minor != other.minor:
            return self.minor < other.minor
        if self.micro != other.micro:
            return self.micro < other.micro
        if self.prerelease != other.prerelease:
            return self.prerelease
        if other.qualifier:
            return True if not self.qualifier else self.qualifier < other.qualifier

        return False

    def __eq__(self, other):
        if other is None: return False

        return self.major == other.major \
            and self.minor == other.minor \
            and self.micro == other.micro \
            and self.prerelease == other.prerelease \
            and self.qualifier == other.qualifier

    def __gt__(self, other):
        return not self < other and not self == other

    def __ge__(self, other):
        return not self < other

    def __le__(self, other):
        return not other < self

    def __hash__(self):
        return hash(self.major) ^ hash(self.minor) ^ hash(self.micro) ^ hash(self.prerelease) ^ hash(self.qualifier)

    def __str__(self):
        p = '' if not self.qualifier else '-' if self.prerelease else '+'
        q = self.qualifier if self.qualifier else ''
        return "%d.%d.%d%s%s" % (self.major, self.minor, self.micro, p, q)

    def __repr__(self):
        p = '-' if self.prerelease else '+' if self.qualifier else ''
        q = self.qualifier if self.qualifier else ''
        return "Version('%d.%d.%d%s%s')" % (self.major, self.minor, self.micro, p, q)

    def __iter__(self):
        yield self.major
        yield self.minor
        yield self.micro

_GITHUB_RELEASES_URL = "https://api.github.com/repos/%s/%s/releases"

def versionsOnGitHub(owner: str, repos: str) -> tuple:
    """
    Downloads the latest release and pre-release version from a GitHub repository's release-list.

    :raises: requests.RequestException, ValueError
    """
    tags = requests.get(_GITHUB_RELEASES_URL % (owner, repos), verify=False)
    json = tags.json()

    versions = []
    latestRelease = None
    latestPreRelease = None

    for release in json:
        try:
            v = Version(release["tag_name"])
        except ValueError:
            continue

        v.prerelease = v.prerelease or release["prerelease"]
        v.weburl = release["html_url"]

        versions.append(v)
        if v.prerelease:
            if v > latestPreRelease:
                latestPreRelease = v
        elif v > latestRelease:
            latestRelease = v

    versions = sorted(versions)

    return (versions, latestRelease, latestPreRelease)
