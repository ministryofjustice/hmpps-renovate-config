# HMPPS Renovate Config

Shared configuration for [Renovate](https://docs.renovatebot.com) to be used in HMPPS projects

Steps to setup Renovate

1. Enable Renovate in your project's repository
2. Renovate Bot will automatically create a PR called _Configure Renovate_
3. In that PR update `renovate.json` with the following - change `node` for other named presets:
```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    "github>ministryofjustice/hmpps-renovate-config:node"
  ]
}
```

You can read more about sharable config presets on the [Renovate docs](https://docs.renovatebot.com/config-presets/)

## Renovate Config

Renovate has a lot of [Configuration Options](https://docs.renovatebot.com/configuration-options/) and the configuration in this project is relatively light touch to allow teams to setup what works for them.  

### Overriding

You can override the configuration defined in `base.json` or the other presets by setting them in your project's `renovate.json`

In `base.json` we set `"printConfig": true` which logs the full resolved config in the [GitHub App run logs](https://app.renovatebot.com/dashboard#github/ministryofjustice)

### Grouping by release type, per package manager

To reduce the number of PRs raised folks can use the following the group updates for all minor and patch NPM dependencies:  

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

### Package stability days

To reduce the noise of PRs being raised as soon as new versions of dependencies are released, you can configure the stability days. This is treated as a check in GitHub and works well in conjunction with `"prCreation": "not-pending"`

Read more on the [Renovate docs](https://docs.renovatebot.com/configuration-options/#stabilitydays)

```json
"packageRules": [
  {
    "matchManagers": ["npm"],
    "stabilityDays": 3
  }
]
```

### Pinning versions

Useful to ensure Renovate does not bump dependencies. One way of doing this is using the following, read more on the [Renovate docs](https://docs.renovatebot.com/configuration-options/#allowedversions)

```json
"packageRules": [
  {
    "matchDatasources": ["docker"],
    "allowedVersions": "19-jre-jammy"
  }
]
```
