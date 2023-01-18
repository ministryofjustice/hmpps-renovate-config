# HMPPS Renovate Config

Shared configuration for [Renovate](https://docs.renovatebot.com) to be used in HMPPS projects - [find places where folks are using it](https://github.com/search?q=user%3Aministryofjustice+hmpps-renovate-config&type=code&ref=advsearch&l=&l=)

## Steps to setup Renovate

1. Enable Renovate in your project's repository
   - You can request this on #ask-operations-engineering, see [example](https://mojdt.slack.com/archives/C01BUKJSZD4/p1666077244584669)
2. Renovate Bot will automatically create a PR called _Configure Renovate_
3. In that PR update `renovate.json` with the following. Change `node` for other named presets and add any other configuration you like:
   ```json
   {
     "$schema": "https://docs.renovatebot.com/renovate-schema.json",
     "extends": [
       "github>ministryofjustice/hmpps-renovate-config:node"
     ]
   }
   ```
4. Merge the PR and Renovate is up and running! 🎉

You can read more about sharable config presets on the [Renovate docs](https://docs.renovatebot.com/config-presets/)

## Renovate Config

Renovate has a lot of [Configuration Options](https://docs.renovatebot.com/configuration-options/) and the configuration in this project is relatively light touch to allow teams to setup what works for them.

### Overriding

You can override any of the configuration defined in `base.json` or the other presets by setting them in your repositories `renovate.json`

To help teams see what configuration Renovate is using we have set `"printConfig": true` in `base.json` which logs the full resolved config each of the [GitHub App run logs](https://app.renovatebot.com/dashboard#github/ministryofjustice)

### Grouping by release type, per package manager

To reduce the number of PRs raised teams can use the following the group updates for all minor and patch NPM dependencies:  

```json
"packageRules": [
  {
    "matchManagers": ["npm"],
    "matchUpdateTypes": ["minor", "patch"],
    "groupName": "all non major NPM dependencies",
    "groupSlug": "all-npm-minor-patch"
  }
]
```

You can switch `matchManagers` for any of the other [Renovate Managers](https://docs.renovatebot.com/modules/manager/)

### Package stability days

To reduce the noise of PRs being raised as soon as new versions of dependencies are released, teams can configure the stability days, read more on the [Renovate docs](https://docs.renovatebot.com/configuration-options/#stabilitydays)

This is treated as a check in GitHub and works well in conjunction with `"prCreation": "not-pending"` - see [Renovate docs](https://docs.renovatebot.com/configuration-options/#prcreation) for that)

```json
"packageRules": [
  {
    "matchManagers": ["npm"],
    "stabilityDays": 3
  }
]
```

It is possible to override this for dependencies with vulnerability alerts, read more on the [Renovate docs](https://docs.renovatebot.com/configuration-options/#vulnerabilityalerts)

```json
"vulnerabilityAlerts": {
  "stabilityDays": 0
}
```

### Pinning versions

Useful to ensure Renovate does not bump certain dependencies, as you may not wish to use the latest major version. One way of achieving this is using `allowedVersions`, read more on the [Renovate docs](https://docs.renovatebot.com/configuration-options/#allowedversions)

```json
"packageRules": [
  {
    "matchDatasources": ["docker"],
    "allowedVersions": "19-jre-jammy"
  }
]
```

### Assigning reviews

Assigning Renovate PRs to folks can help with visibility to ensure they are approved and merged in a timely manner. Ways to achieve this:
 - If repositories use [GitHub CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) then they can use the config [assigneesFromCodeOwners](https://docs.renovatebot.com/configuration-options/#assigneesfromcodeowners)
 - [reviewers](https://docs.renovatebot.com/configuration-options/#reviewers) however as per [comment on JIRA](https://dsdmoj.atlassian.net/browse/HEAT-39?focusedCommentId=257543) was not able to get assigning to a GitHub team working, and instead assigned to individuals with `"reviewers": ["neilmendum"]`
