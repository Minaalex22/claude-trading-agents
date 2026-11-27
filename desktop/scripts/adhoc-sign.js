// Signs the Mac app ad-hoc ("-") after packing, the whole bundle at once.
// electron-builder's own ad-hoc signing left a broken signature ("code has no resources but signature
// indicates they must be present"), so macOS said "JARVIS Trading is damaged". Apple-chip Macs refuse to
// run an app whose signature is broken, so this has to be right.
const { execFileSync } = require("child_process");
const path = require("path");

exports.default = async function adhocSign(context) {
  if (context.electronPlatformName !== "darwin") return;
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  execFileSync("codesign", ["--force", "--deep", "--sign", "-", app], { stdio: "inherit" });
  execFileSync("codesign", ["--verify", "--deep", "--strict", "--verbose=2", app], { stdio: "inherit" });
};
