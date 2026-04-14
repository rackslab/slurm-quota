%global pkg_version %{?pkg_version}%{!?pkg_version:1.0.0}
%global pkg_release %{?pkg_release}%{!?pkg_release:1}

Name:           slurm-quota
Version:        %{pkg_version}
Release:        %{pkg_release}%{?dist}
Summary:        Slurm quota management tool

License:        MIT
URL:            https://github.com/rackslab/slurm-quota
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  asciidoctor
BuildRequires:  bash-completion
BuildRequires:  python3-devel
BuildRequires:  python3dist(pytest)
BuildRequires:  python3dist(flask)
BuildRequires:  python3dist(jinja2)
BuildRequires:  systemd-rpm-macros

Requires:       python3

%description
slurm-quota assigns CPU/GPU minute quotas to Slurm users and accounts and
enforces them on submission and completion paths.

%package controller
Summary:        Controller-side files for slurm-quota
Requires:       %{name} = %{version}-%{release}
Requires:       lua-dbi
Requires:       lua-posix
Requires:       sqlite
Requires(post): /usr/bin/python3

%description controller
Controller package for slurm-quota with Slurm integration files, systemd units,
logrotate policy and the migration helper.

%package web
Summary:        Web dashboard for slurm-quota
Requires:       %{name} = %{version}-%{release}
Requires:       python3
Requires:       python3dist(flask)
Requires:       python3dist(jinja2)

%description web
Web dashboard package for slurm-quota. It provides the `slurm-quota-web` Flask
WSGI application and static/template assets. It can run standalone with Flask's
built-in server for testing, or behind Apache/mod_wsgi in production.

%prep
%autosetup

%build
# Stamp runtime APP_VERSION with package version
sed -i "s|^APP_VERSION = \".*\"$|APP_VERSION = \"%{version}\"|g" slurm-quota
# Rewrite /usr/local/bin references to the final %{_bindir} path
sed -i 's|/usr/local/bin/slurm-quota|%{_bindir}/slurm-quota|g' slurm-quota-charge-wrapper
sed -i 's|/usr/local/bin/slurm-quota|%{_bindir}/slurm-quota|g' slurm-quota.service
# Generate man page from source AsciiDoc file.
asciidoctor -a revnumber=%{version} -b manpage -o slurm-quota.1 man/slurm-quota.1.adoc
asciidoctor -a revnumber=%{version} -b manpage -o slurm-quota-web.1 man/slurm-quota-web.1.adoc

%check
cd %{_builddir}/%{buildsubdir}
TEST_USER=slurmquota_test
if ! id -u "${TEST_USER}" >/dev/null 2>&1; then
  useradd -r -M -s /sbin/nologin "${TEST_USER}" >/dev/null 2>&1 || true
fi
if id -u "${TEST_USER}" >/dev/null 2>&1; then
  su -s /bin/bash -c 'PYTHONPATH=%{_builddir}/%{buildsubdir} %pytest -q --override-ini="addopts="' "${TEST_USER}"
else
  PYTHONPATH=%{_builddir}/%{buildsubdir} %pytest -q --override-ini="addopts="
fi

%install
install -Dm0755 slurm-quota %{buildroot}%{_bindir}/slurm-quota
install -Dm0644 slurm-quota.bash-completion %{buildroot}%{bash_completions_dir}/slurm-quota
install -Dm0644 slurm-quota.1 %{buildroot}%{_mandir}/man1/slurm-quota.1
install -Dm0644 slurm-quota-web.1 %{buildroot}%{_mandir}/man1/slurm-quota-web.1
install -Dm0755 slurm-quota-charge-wrapper %{buildroot}%{_sysconfdir}/slurm/slurm-quota-charge-wrapper
install -Dm0644 job_submit.lua %{buildroot}%{_sysconfdir}/slurm/job_submit.lua
install -Dm0644 slurm-quota.service %{buildroot}%{_unitdir}/slurm-quota.service
install -Dm0644 slurm-quota.socket %{buildroot}%{_unitdir}/slurm-quota.socket
install -Dm0644 slurm-quota-charge.logrotate %{buildroot}%{_sysconfdir}/logrotate.d/slurm-quota-charge
install -Dm0755 migrate-slurm-quota %{buildroot}%{_libexecdir}/slurm-quota/migrate-slurm-quota
install -Dm0755 slurm-quota-web %{buildroot}%{_libexecdir}/slurm-quota/slurm-quota-web
install -d %{buildroot}%{_localstatedir}/log/slurm/charge
install -d %{buildroot}%{_sharedstatedir}/state/slurm-quota
install -d %{buildroot}%{_datadir}/slurm-quota-web/templates
install -d %{buildroot}%{_datadir}/slurm-quota-web/static
install -m 0644 webapp/templates/*.html %{buildroot}%{_datadir}/slurm-quota-web/templates/
install -m 0644 webapp/static/*.css %{buildroot}%{_datadir}/slurm-quota-web/static/

%post controller
DB_PATH=%{_sharedstatedir}/state/slurm-quota/slurm-quota.db
MIGRATE=%{_libexecdir}/slurm-quota/migrate-slurm-quota

if [ -x "${MIGRATE}" ] && [ -f "${DB_PATH}" ]; then
    "${MIGRATE}" || exit 1
fi

%files
%license LICENSE
%doc README.md
%{_bindir}/slurm-quota
%{bash_completions_dir}/slurm-quota
%{_mandir}/man1/slurm-quota.1*
%{_mandir}/man1/slurm-quota-web.1*

%files controller
%{_sysconfdir}/slurm/slurm-quota-charge-wrapper
%config(noreplace) %{_sysconfdir}/slurm/job_submit.lua
%{_unitdir}/slurm-quota.service
%{_unitdir}/slurm-quota.socket
%config(noreplace) %{_sysconfdir}/logrotate.d/slurm-quota-charge
%{_libexecdir}/slurm-quota/migrate-slurm-quota
%dir %attr(0755,slurm,slurm) %{_localstatedir}/log/slurm/charge
%dir %attr(0755,slurm,slurm) %{_sharedstatedir}/state/slurm-quota

%files web
%{_libexecdir}/slurm-quota/slurm-quota-web
%{_datadir}/slurm-quota-web
