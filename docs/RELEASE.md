# How to release SCRIPT Capture

This document gives a proportionate release process for SCRIPT Capture, the iPad app. A study uses only a release that went through this process. The process sets the version and build numbers, puts the source revision into every journal, signs the build, and limits who can install it. It ends with the record that each site keeps about the release that it uses. The [last section](#build-script-bench-for-the-bench-study) tells how to build SCRIPT Bench, the Mac app of the bench study.

SCRIPT is a research platform, not a medical device. This process is not a claim of conformity with IEC 62304 or any other standard.

## Set the version and the build number

The app has two numbers. `app/ScriptApp.xcodeproj` holds both, and every journal records both in `sessionOpened.app`.

| Number | Build setting | Journal field | Now |
| --- | --- | --- | --- |
| Version | `MARKETING_VERSION` | `app.version` | 0.1.0 |
| Build number | `CURRENT_PROJECT_VERSION` | `app.build` | 1 |

1. Raise the version for each release that a participant, an operator, or an analyst can notice. Examples are a new screen, a changed task, a changed instrument, or a change to the recording format.
2. Raise the build number for each build that you distribute. Never use a build number two times for one version.
3. Give both numbers on the command line of the release build, as the archive command below shows.

The journal also records versions that do not come from the release: the recording format, the battery, and the digest of the study configuration. A release does not change them.

## Put the source revision into every journal

The app reads the source revision from the `SCRIPTSourceRevision` key of its `Info.plist`. The build setting `SCRIPT_SOURCE_REVISION` fills that key. The project sets it to `unknown`, and the journal records an empty value as `unknown`.

1. Make the release from a commit that `./check` passed, with no change in the working tree.
2. Tag the commit with the version, for example `v0.2.0`.
3. Give `SCRIPT_SOURCE_REVISION` the short commit identifier in the release build.
4. After you install the release, run a qualification session. Check that its journal records the same revision in `app.sourceRevision`.

`./check` adds `-dirty` to the revision when the working tree has changes. A release revision never ends with `-dirty`.

## Sign the build with a developer team

A release needs a team in the Apple Developer Program. A personal team is for the [device test](DEVICE_TESTING.md) only. It cannot distribute the app, and its builds stop working after 7 days.

1. In the Apple Developer Program team, register a bundle identifier for the app. The project uses `com.dvgyl.script.capture`.
2. In App Store Connect, create the app record with that bundle identifier.
3. Find the team identifier of the developer team.
4. On a Mac with Xcode 27.2 or later, run the archive command below from the repository folder.
5. Open the archive in the Xcode Organizer, and distribute it with the method that the next section names.

```sh
xcodebuild archive -project app/ScriptApp.xcodeproj -scheme ScriptApp -configuration Release \
  -destination "generic/platform=iOS" -archivePath "<release folder>/ScriptApp-<version>-<build>.xcarchive" \
  -allowProvisioningUpdates DEVELOPMENT_TEAM=<team identifier> \
  MARKETING_VERSION=<version> CURRENT_PROJECT_VERSION=<build> \
  SCRIPT_SOURCE_REVISION="$(git rev-parse --short HEAD)"
```

Keep the archive with the release record. The project leaves `DEVELOPMENT_TEAM` empty, so the command line gives it and the project file stays unchanged.

## Distribute the build to enrolled participants only

The app holds the text of the DASS-21. Its licence permits use with enrolled participants only, as `contract/instruments.json` records. So the app must never reach a person outside the study.

- Do not publish the app on the App Store.
- Do not make a public TestFlight link.
- Install the app only on study iPads, or on the devices of enrolled participants.

Use one of these channels:

- **TestFlight.** Upload the build to App Store Connect. Invite each tester by name: the Apple Account that a study iPad uses, or an enrolled participant. A TestFlight build expires 90 days after the upload, so plan the next build before then.
- **Device management.** Give the build to the site's device management service, which installs it on the study iPads only.

Follow Apple's current instructions for the channel that you choose.

## Release checklist

Complete each item, and record the result in the release record.

1. Run `./check` on the release commit. Record the last line that it prints.
2. Run the [device test](DEVICE_TESTING.md) with this build on each iPad model and Pencil model that the study uses.
3. Review the [hazard log](HAZARDS.md). Check each controls table against the code of the release, and update the grades.
4. Review the controls table of the [data governance](DATA_GOVERNANCE.md#controls-that-script-has) document against the code of the release.
5. Get the written acceptance of each residual risk from the sponsor.
6. Record the version, the build number, the source revision, and the tag.
7. Archive, sign, and distribute the build.

## Record the release at each site

Each site keeps a record of the release that it uses. The record lets an analyst match each session to the software that made it.

| Item | Where to find it |
| --- | --- |
| Version, build number, and source revision | The release record. Every journal records them in `sessionOpened.app`. |
| Distribution channel and installation date | The site's device records |
| Device asset code of each iPad with this release | The site's device records. Every journal records the asset code in `sessionOpened.device`. |
| Device test result for each iPad model and Pencil model | The device test record |
| Qualification of each iPad, Pencil, iPadOS version, and app version | The qualification reports, as the [study guide](STUDY_GUIDE.md#qualify-each-device) states |
| Digest of the study configuration | Home on each iPad, and `study.configSHA256` in every journal |
| Repository revision of the scriptkin that reads the exports | The analyst's records |

The QC report counts the sessions of the study store by app version. Use it to check that every session came from a release that the site recorded.

## Build SCRIPT Bench for the bench study

SCRIPT Bench records the Wacom tablet of the [bench protocol](BENCH_PROTOCOL.md). It records no person, and it is not distributed. The `ScriptBench` target of `app/ScriptApp.xcodeproj` has its own two numbers, and every journal of SCRIPT Bench records both in `sessionOpened.app`.

| Number | Build setting | Journal field | Now |
| --- | --- | --- | --- |
| Version | `MARKETING_VERSION` of the `ScriptBench` target | `app.version` | 0.1.0 |
| Build number | `CURRENT_PROJECT_VERSION` of the `ScriptBench` target | `app.build` | 1 |

1. Make the build on the bench Mac from a commit that `./check` passed, with no change in the working tree.
2. Run the build command below. Use the Release configuration.
3. Run the [tablet test](DEVICE_TESTING.md#part-b-the-wacom-tablet) with this build.
4. Record the version, the build number, and the source revision from the first journal of the build.

```sh
xcodebuild build -project app/ScriptApp.xcodeproj -scheme ScriptBench -configuration Release \
  -destination "platform=macOS,arch=$(uname -m)" -derivedDataPath "<release folder>/ScriptBench-<version>-<build>" \
  MARKETING_VERSION=<version> CURRENT_PROJECT_VERSION=<build> SCRIPT_SOURCE_REVISION="$(git rev-parse --short HEAD)"
```

The app is `Build/Products/Release/ScriptBench.app` in the folder of `-derivedDataPath`. A Debug build accepts the launch argument `-ScriptBenchWithoutTablet`, which skips the clock check. So use only a Release build for bench sessions. The target signs the app ad hoc (`CODE_SIGN_IDENTITY` is `-`), so the build needs no developer team. The bench lock records `app.build` and `app.sourceRevision` of SCRIPT Bench (`docs/BENCH_PROTOCOL.md` section 14).
