{
  description = "A Nix-flake-based Python development environment";

  inputs.nixpkgs.url = "nixpkgs/nixos-unstable";

  outputs = {self, ...} @ inputs: let
    inherit (inputs.nixpkgs) lib;

    supportedSystems = [
      "x86_64-linux"
      "aarch64-linux"
      "aarch64-darwin"
    ];

    forEachSupportedSystem = f:
      lib.genAttrs supportedSystems (
        system:
          f {
            inherit system;
            pkgs = import inputs.nixpkgs {inherit system;};
          }
      );

    version = "3.14";
  in {
    devShells = forEachSupportedSystem (
      {
        pkgs,
        system,
      }: let
        concatMajorMinor = v:
          lib.pipe v [
            lib.versions.splitVersion
            (lib.sublist 0 2)
            lib.concatStrings
          ];

        python = pkgs."python${concatMajorMinor version}";
      in {
        default = pkgs.mkShellNoCC {
          venvDir = ".venv";

          postShellHook = ''
            venvVersionWarn() {
            	local venvVersion
            	venvVersion="$("$venvDir/bin/python" -c 'import platform; print(platform.python_version())')"

            	[[ "$venvVersion" == "${python.version}" ]] && return

            	cat <<EOF
            Warning: Python version mismatch: [$venvVersion (venv)] != [${python.version}]
                     Delete '$venvDir' and reload to rebuild for version ${python.version}
            EOF
            }

            venvVersionWarn
          '';

          packages =
            (with python.pkgs; [
              venvShellHook
              pip
              jinja2
              sqlalchemy
              flask
              flask-sqlalchemy
              python-dotenv
              pandas
              numpy
              requests
            ])
            ++ [
              self.formatter.${system}
              pkgs.sqlite
              pkgs.djlint
            ];
        };
      }
    );

    formatter = forEachSupportedSystem ({pkgs, ...}: pkgs.nixfmt);
  };
}
