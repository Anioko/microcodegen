// archiet-microcodegen-dotnet v0.1.0
// PRD text -> ASP.NET Core 8 app -> ZIP. Pure .NET BCL. <1400 LOC.
//
// Stage 1: ParsePrd(text)              -> Manifest (language-agnostic)
// Stage 2: ManifestToGenome(manifest)  -> Genome   (ArchiMate 3.2 typed)
// Stage 3: RenderGenome(genome)        -> Dictionary<path, content>
// Stage 4: PackZip(files)              -> byte[]
//
// Zero NuGet dependencies. Inspired by Karpathy's micrograd.

using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

// ─── CLI ─────────────────────────────────────────────────────────────────────
var cliArgs = Environment.GetCommandLineArgs().Skip(1).ToArray();
if (cliArgs.Length == 0 || cliArgs[0] is "-h" or "--help")
{
    Console.WriteLine("archiet-microcodegen-dotnet v0.1.0");
    Console.WriteLine("Usage: archiet-microcodegen-dotnet <prd.md> [--out <dir>] [--zip <file>]");
    return;
}
var prdPath = cliArgs[0];
if (!File.Exists(prdPath)) { Console.Error.WriteLine($"Error: PRD not found: {prdPath}"); return; }

string? outDir = null, zipOut = null;
for (int i = 1; i < cliArgs.Length; i++)
{
    if (cliArgs[i] == "--out" && i + 1 < cliArgs.Length) outDir = cliArgs[++i];
    if (cliArgs[i] == "--zip" && i + 1 < cliArgs.Length) zipOut = cliArgs[++i];
}
if (outDir is null && zipOut is null) outDir = "./output";

var manifest = Stage1.ParsePrd(File.ReadAllText(prdPath));
var genome   = Stage2.ToGenome(manifest);
var rendered = Stage3.Render(genome);

if (zipOut  is not null) { File.WriteAllBytes(zipOut, Stage4.PackZip(rendered)); Console.WriteLine($"ZIP: {zipOut} ({rendered.Count} files)"); }
if (outDir  is not null) { Stage4.WriteDisk(rendered, outDir); Console.WriteLine($"Done. cd {outDir} && cp .env.example .env && docker compose up"); }

// ─── Stage 1: ParsePrd ───────────────────────────────────────────────────────
record Field(string Name, string Type, bool Required);
record Module(string Name, string Archimate, List<Field> Fields);
record Manifest(string Name, List<Module> Entities, List<string> Stories, List<string> Integrations);
record Genome(string SolutionName, string Slug, string Version, string Language,
              List<Module> Modules, List<string> Integrations, List<string> UserStories);

static class Stage1
{
    static readonly string[] Skip = { "User","Auth","Admin","Api","The" };
    static readonly string[] KnownIntegrations = { "stripe","sendgrid","twilio","slack","github","google","aws","s3","cloudinary","firebase" };

    public static Manifest ParsePrd(string text)
    {
        var nameM = Regex.Match(text, @"^#\s+(.+)", RegexOptions.Multiline);
        var name  = nameM.Success ? nameM.Groups[1].Value.Trim() : "MyApp";

        var entities = new List<Module>();
        var secM = Regex.Match(text, @"^#{1,3}\s*(?:entities|data models|domain models)[^\n]*", RegexOptions.IgnoreCase | RegexOptions.Multiline);
        if (secM.Success)
        {
            var sec = text[secM.Index..];
            var endM = Regex.Match(sec, @"\n#{1,3}\s+(?!entities|data|domain)", RegexOptions.IgnoreCase);
            if (endM.Success) sec = sec[..endM.Index];

            foreach (Match em in Regex.Matches(sec, @"^[\s\-\*]*([A-Z][a-zA-Z0-9]{1,40})\*{0,2}[ \t]*(?::|—|-| )", RegexOptions.Multiline))
            {
                var en = em.Groups[1].Value;
                if (Skip.Contains(en)) continue;
                var fields = new List<Field>();
                var epos   = sec.IndexOf(en, StringComparison.Ordinal);
                if (epos >= 0)
                {
                    var chunk = sec.Substring(epos, Math.Min(600, sec.Length - epos));
                    foreach (Match fm in Regex.Matches(chunk, @"^\s+[-*]\s*([a-z_][a-z0-9_]{0,40})\s*[:—]\s*([a-zA-Z]+)([^\n]*)", RegexOptions.Multiline))
                        fields.Add(new Field(fm.Groups[1].Value, fm.Groups[2].Value.ToLower(),
                            fm.Groups[3].Value.ToLower().Contains("required") || fm.Groups[3].Value.Contains("*")));
                }
                entities.Add(new Module(en, "DataObject", fields));
            }
        }

        var stories = Regex.Matches(text, @"As a[n]?\s+\w+,\s*I want[^.\n]+", RegexOptions.IgnoreCase)
                           .Select(m => m.Value.Trim()).ToList();
        var lower   = text.ToLower();
        var integrations = KnownIntegrations.Where(k => lower.Contains(k)).ToList();
        return new Manifest(name, entities, stories, integrations);
    }
}

// ─── Stage 2: ManifestToGenome ────────────────────────────────────────────────
static class Stage2
{
    public static Genome ToGenome(Manifest m)
    {
        var slug = Regex.Replace(m.Name.ToLower(), @"[^a-z0-9]+", "-").Trim('-');
        var baseFields = new List<Field>
        {
            new("Id",        "bigint",    true),
            new("UserId",    "bigint",    true),
            new("CreatedAt", "timestamp", false),
            new("UpdatedAt", "timestamp", false),
        };
        var modules = m.Entities.Select(e =>
            new Module(e.Name, "DataObject", [..baseFields, ..e.Fields])).ToList();
        return new Genome(m.Name, slug, "0.1.0", "dotnet", modules, m.Integrations, m.Stories);
    }
}

// ─── Helpers ─────────────────────────────────────────────────────────────────
static class H
{
    public static string Pascal(string s)
    {
        if (string.IsNullOrEmpty(s)) return s;
        return string.Concat(s.Split(['_','-',' ']).Select(w =>
            w.Length > 0 ? char.ToUpper(w[0]) + w[1..] : w));
    }
    public static string Camel(string s)  { var p = Pascal(s); return p.Length > 0 ? char.ToLower(p[0]) + p[1..] : p; }
    public static string Snake(string s)  => Regex.Replace(s, "([a-z0-9])([A-Z])", "$1_$2").ToLower();
    public static string Plural(string s) => s.EndsWith('y') ? s[..^1] + "ies" : s.EndsWith('s') || s.EndsWith('x') || s.EndsWith('z') ? s + "es" : s + "s";
    public static string Fill(string t, Dictionary<string, string> v)
    {
        foreach (var (k, val) in v) t = t.Replace("{{" + k + "}}", val);
        return t;
    }
    public static string EfType(string t) => t switch
    {
        "text" or "description"    => "string?",
        "int" or "integer"         => "int",
        "bigint"                   => "long",
        "bool" or "boolean"        => "bool",
        "date"                     => "DateOnly?",
        "decimal" or "float"       => "decimal?",
        "timestamp"                => "DateTime",
        _                          => "string?",
    };
}

// ─── Stage 3: RenderGenome ────────────────────────────────────────────────────
static class Stage3
{
    public static Dictionary<string, string> Render(Genome g)
    {
        var files = new Dictionary<string, string>();
        var name  = g.SolutionName;
        var slug  = H.Pascal(g.Slug.Replace("-","_"));
        var mods  = g.Modules;

        // .csproj
        files[$"{slug}.csproj"] = $$"""
            <Project Sdk="Microsoft.NET.Sdk.Web">
              <PropertyGroup>
                <TargetFramework>net8.0</TargetFramework>
                <Nullable>enable</Nullable>
                <ImplicitUsings>enable</ImplicitUsings>
                <RootNamespace>{{slug}}</RootNamespace>
              </PropertyGroup>
              <ItemGroup>
                <PackageReference Include="Microsoft.EntityFrameworkCore.Design" Version="8.0.*" />
                <PackageReference Include="Npgsql.EntityFrameworkCore.PostgreSQL" Version="8.0.*" />
                <PackageReference Include="BCrypt.Net-Next" Version="4.0.*" />
              </ItemGroup>
            </Project>
            """;

        // Program.cs
        var modelUsings = string.Join("\n", mods.Select(m => $"using {slug}.Models;"));
        files["Program.cs"] = $$"""
            using Microsoft.EntityFrameworkCore;
            using {{slug}}.Data;
            using {{slug}}.Services;
            {{modelUsings}}

            var builder = WebApplication.CreateBuilder(args);
            var connStr = Environment.GetEnvironmentVariable("DATABASE_URL")
                       ?? builder.Configuration.GetConnectionString("Default")
                       ?? throw new InvalidOperationException("DATABASE_URL not set.");
            builder.Services.AddDbContext<AppDbContext>(o => o.UseNpgsql(connStr));
            builder.Services.AddSingleton<JwtService>();
            builder.Services.AddControllers();
            builder.Services.AddCors(o => o.AddDefaultPolicy(p => p.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod()));

            var app = builder.Build();
            app.UseCors();
            app.UseMiddleware<{{slug}}.Auth.JwtMiddleware>();
            app.MapControllers();
            using (var scope = app.Services.CreateScope())
                scope.ServiceProvider.GetRequiredService<AppDbContext>().Database.Migrate();
            app.Run();
            """;

        // Data/AppDbContext.cs
        var dbSets = string.Join("\n    ", mods.Select(m => $"public DbSet<{H.Pascal(m.Name)}> {H.Plural(H.Pascal(m.Name))} {{ get; set; }}"));
        files["Data/AppDbContext.cs"] = $$"""
            using Microsoft.EntityFrameworkCore;
            using {{slug}}.Models;
            namespace {{slug}}.Data;
            public class AppDbContext(DbContextOptions<AppDbContext> opts) : DbContext(opts)
            {
                public DbSet<User> Users { get; set; }
                {{dbSets}}
            }
            """;

        // Models/User.cs
        files["Models/User.cs"] = $$"""
            namespace {{slug}}.Models;
            public class User
            {
                public long Id { get; set; }
                public string Name { get; set; } = "";
                public string Email { get; set; } = "";
                public string PasswordHash { get; set; } = "";
                public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
            }
            """;

        // Services/JwtService.cs — uses System.Text.Json to avoid nested raw-string-literal issues
        files["Services/JwtService.cs"] = $$"""
            using System.Security.Cryptography;
            using System.Text;
            using System.Text.Json;
            namespace {{slug}}.Services;
            public class JwtService
            {
                private readonly string _secret = Environment.GetEnvironmentVariable("JWT_SECRET")
                    ?? throw new InvalidOperationException("JWT_SECRET not set.");
                private readonly long _ttl = long.Parse(Environment.GetEnvironmentVariable("JWT_TTL_SEC") ?? "604800");

                private static string B64Url(byte[] b) =>
                    Convert.ToBase64String(b).TrimEnd('=').Replace('+','-').Replace('/','_');

                public string Encode(long userId, string email)
                {
                    var header  = B64Url(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { typ = "JWT", alg = "HS256" })));
                    var payload = B64Url(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { sub = userId, email, exp = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + _ttl })));
                    var sig     = B64Url(new HMACSHA256(Encoding.UTF8.GetBytes(_secret)).ComputeHash(Encoding.UTF8.GetBytes($"{header}.{payload}")));
                    return $"{header}.{payload}.{sig}";
                }

                public (long UserId, string Email)? Decode(string token)
                {
                    var pts = token.Split('.');
                    if (pts.Length != 3) return null;
                    var expected = B64Url(new HMACSHA256(Encoding.UTF8.GetBytes(_secret))
                        .ComputeHash(Encoding.UTF8.GetBytes($"{pts[0]}.{pts[1]}")));
                    if (!CryptographicOperations.FixedTimeEquals(
                        Encoding.UTF8.GetBytes(expected), Encoding.UTF8.GetBytes(pts[2]))) return null;
                    var json = Encoding.UTF8.GetString(Convert.FromBase64String(
                        pts[1].Replace('-','+').Replace('_','/').PadRight(pts[1].Length + (4 - pts[1].Length % 4) % 4, '=')));
                    using var doc = JsonDocument.Parse(json);
                    var root = doc.RootElement;
                    if (!root.TryGetProperty("sub", out var subEl) || !root.TryGetProperty("exp", out var expEl)) return null;
                    if (expEl.GetInt64() < DateTimeOffset.UtcNow.ToUnixTimeSeconds()) return null;
                    var emlStr = root.TryGetProperty("email", out var emlEl) ? emlEl.GetString() ?? "" : "";
                    return (subEl.GetInt64(), emlStr);
                }
            }
            """;

        // Auth/JwtMiddleware.cs
        files["Auth/JwtMiddleware.cs"] = $$"""
            using {{slug}}.Services;
            namespace {{slug}}.Auth;
            public class JwtMiddleware(RequestDelegate next)
            {
                public async Task InvokeAsync(HttpContext ctx, JwtService jwt)
                {
                    var skip = ctx.Request.Path.StartsWithSegments("/api/auth/register")
                            || ctx.Request.Path.StartsWithSegments("/api/auth/login");
                    if (!skip)
                    {
                        var token = ctx.Request.Cookies["access_token"];
                        if (token is null) { ctx.Response.StatusCode = 401; await ctx.Response.WriteAsJsonAsync(new { error = "unauthenticated", message = "No auth cookie." }); return; }
                        var claim = jwt.Decode(token);
                        if (claim is null) { ctx.Response.StatusCode = 401; await ctx.Response.WriteAsJsonAsync(new { error = "unauthenticated", message = "Invalid or expired token." }); return; }
                        ctx.Items["UserId"]    = claim.Value.UserId;
                        ctx.Items["UserEmail"] = claim.Value.Email;
                    }
                    await next(ctx);
                }
            }
            """;

        // Controllers/AuthController.cs
        files["Controllers/AuthController.cs"] = $$"""
            using Microsoft.AspNetCore.Mvc;
            using Microsoft.EntityFrameworkCore;
            using {{slug}}.Data;
            using {{slug}}.Models;
            using {{slug}}.Services;
            namespace {{slug}}.Controllers;
            [ApiController, Route("api/auth")]
            public class AuthController(AppDbContext db, JwtService jwt) : ControllerBase
            {
                [HttpPost("register")]
                public async Task<IActionResult> Register([FromBody] AuthDto dto)
                {
                    if (await db.Users.AnyAsync(u => u.Email == dto.Email))
                        return UnprocessableEntity(new { error = "validation_error", message = "Email already in use." });
                    var user = new User { Name = dto.Name ?? dto.Email, Email = dto.Email, PasswordHash = BCrypt.Net.BCrypt.HashPassword(dto.Password) };
                    db.Users.Add(user); await db.SaveChangesAsync();
                    SetCookie(jwt.Encode(user.Id, user.Email));
                    return Created($"/api/auth/me", new { user = new { user.Id, user.Name, user.Email } });
                }

                [HttpPost("login")]
                public async Task<IActionResult> Login([FromBody] AuthDto dto)
                {
                    var user = await db.Users.FirstOrDefaultAsync(u => u.Email == dto.Email);
                    if (user is null || !BCrypt.Net.BCrypt.Verify(dto.Password, user.PasswordHash))
                        return Unauthorized(new { error = "invalid_credentials", message = "Wrong email or password." });
                    SetCookie(jwt.Encode(user.Id, user.Email));
                    return Ok(new { user = new { user.Id, user.Name, user.Email } });
                }

                [HttpDelete("logout")]
                public IActionResult Logout() { Response.Cookies.Delete("access_token"); return Ok(new { message = "Logged out." }); }

                [HttpGet("me")]
                public async Task<IActionResult> Me()
                {
                    var uid = (long)(HttpContext.Items["UserId"] ?? 0L);
                    var u   = await db.Users.FindAsync(uid);
                    return u is null ? NotFound(new { error = "not_found" }) : Ok(new { user = new { u.Id, u.Name, u.Email } });
                }

                private void SetCookie(string token) =>
                    Response.Cookies.Append("access_token", token, new CookieOptions
                    { HttpOnly = true, SameSite = SameSiteMode.Lax, Expires = DateTimeOffset.UtcNow.AddDays(7) });
            }
            public record AuthDto(string? Name, string Email, string Password);
            """;

        // per-entity
        foreach (var mod in mods)
        {
            var pa  = H.Pascal(mod.Name);
            var pap = H.Plural(pa);
            var userFields  = mod.Fields.Where(f => f.Name is not ("Id" or "CreatedAt" or "UpdatedAt")).ToList();
            var propLines   = string.Join("\n    ", userFields.Select(f =>
                f.Name == "UserId" ? "public long UserId { get; set; }"
                                   : $"public {H.EfType(f.Type)} {H.Pascal(f.Name)} {{ get; set; }}"));
            var dtoLines    = string.Join("\n    ", userFields.Where(f => f.Name != "UserId")
                .Select(f => $"public {H.EfType(f.Type)} {H.Pascal(f.Name)} {{ get; set; }}"));
            var applyLines  = string.Join("\n        ", userFields.Where(f => f.Name != "UserId")
                .Select(f => $"e.{H.Pascal(f.Name)} = dto.{H.Pascal(f.Name)};"));

            // Model
            files[$"Models/{pa}.cs"] = $$"""
                namespace {{slug}}.Models;
                public class {{pa}}
                {
                    public long Id { get; set; }
                    {{propLines}}
                    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
                    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;
                }
                """;

            // DTO
            files[$"DTOs/{pa}Dto.cs"] = $$"""
                namespace {{slug}}.DTOs;
                public class {{pa}}Dto
                {
                    {{dtoLines}}
                }
                """;

            // Controller
            files[$"Controllers/{pap}Controller.cs"] = $$"""
                using Microsoft.AspNetCore.Mvc;
                using Microsoft.EntityFrameworkCore;
                using {{slug}}.Data;
                using {{slug}}.DTOs;
                using {{slug}}.Models;
                namespace {{slug}}.Controllers;
                [ApiController, Route("api/{{H.Plural(H.Snake(mod.Name)).Replace("_","-")}}")]
                public class {{pap}}Controller(AppDbContext db) : ControllerBase
                {
                    private long Uid => (long)(HttpContext.Items["UserId"] ?? 0L);

                    [HttpGet]
                    public async Task<IActionResult> Index() =>
                        Ok(await db.{{pap}}.Where(e => e.UserId == Uid).ToListAsync());

                    [HttpPost]
                    public async Task<IActionResult> Create([FromBody] {{pa}}Dto dto)
                    {
                        var e = new {{pa}} { UserId = Uid };
                        Apply(e, dto); db.{{pap}}.Add(e); await db.SaveChangesAsync();
                        return Created($"/api/{{H.Plural(H.Snake(mod.Name)).Replace("_","-")}}/{e.Id}", e);
                    }

                    [HttpGet("{id}")]
                    public async Task<IActionResult> Show(long id)
                    {
                        var e = await db.{{pap}}.FirstOrDefaultAsync(x => x.Id == id && x.UserId == Uid);
                        return e is null ? NotFound(new { error = "not_found" }) : Ok(e);
                    }

                    [HttpPut("{id}")]
                    public async Task<IActionResult> Update(long id, [FromBody] {{pa}}Dto dto)
                    {
                        var e = await db.{{pap}}.FirstOrDefaultAsync(x => x.Id == id && x.UserId == Uid);
                        if (e is null) return NotFound(new { error = "not_found" });
                        Apply(e, dto); e.UpdatedAt = DateTime.UtcNow; await db.SaveChangesAsync();
                        return Ok(e);
                    }

                    [HttpDelete("{id}")]
                    public async Task<IActionResult> Destroy(long id)
                    {
                        var e = await db.{{pap}}.FirstOrDefaultAsync(x => x.Id == id && x.UserId == Uid);
                        if (e is null) return NotFound(new { error = "not_found" });
                        db.{{pap}}.Remove(e); await db.SaveChangesAsync(); return NoContent();
                    }

                    private static void Apply({{pa}} e, {{pa}}Dto dto) { {{applyLines}} }
                }
                """;
        }

        // appsettings.json
        files["appsettings.json"] = """
            {
              "Logging": { "LogLevel": { "Default": "Information", "Microsoft.AspNetCore": "Warning" } },
              "AllowedHosts": "*"
            }
            """;

        // .env.example
        files[".env.example"] = $"""
            DATABASE_URL=Host=db;Database=app;Username=app;Password=changeme
            JWT_SECRET=change-me-jwt-secret-minimum-32-characters
            JWT_TTL_SEC=604800
            ASPNETCORE_URLS=http://+:8080
            ASPNETCORE_ENVIRONMENT=Production
            """;

        // Dockerfile
        files["Dockerfile"] = $"""
            FROM mcr.microsoft.com/dotnet/sdk:8.0-alpine AS build
            WORKDIR /src
            COPY {slug}.csproj .
            RUN dotnet restore
            COPY . .
            RUN dotnet publish -c Release -o /app --no-restore

            FROM mcr.microsoft.com/dotnet/aspnet:8.0-alpine
            WORKDIR /app
            COPY --from=build /app .
            EXPOSE 8080
            ENTRYPOINT ["dotnet", "{slug}.dll"]
            """;

        // docker-compose.yml
        files["docker-compose.yml"] = """
            services:
              app:
                build: .
                ports: ["8080:8080"]
                env_file: .env
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16-alpine
                environment:
                  POSTGRES_DB: app
                  POSTGRES_USER: app
                  POSTGRES_PASSWORD: changeme
                ports: ["5432:5432"]
                volumes: [db_data:/var/lib/postgresql/data]
                healthcheck:
                  test: ["CMD-SHELL", "pg_isready -U app"]
                  interval: 5s
                  timeout: 5s
                  retries: 10
            volumes:
              db_data:
            """;

        // ARCHITECTURE.md
        var arc  = $"# ARCHITECTURE — {name}\n\nGenerated by archiet-microcodegen-dotnet. ArchiMate 3.2 notation.\n\n";
        arc += "## ApplicationComponent\n\n| Component | Technology | Notes |\n|---|---|---|\n";
        arc += "| ApiGateway | ASP.NET Core 8 Routing | Routes API requests |\n";
        arc += "| AuthService | JWT (httpOnly cookie) + BCrypt | register / login / logout |\n";
        foreach (var m in mods) arc += $"| {H.Pascal(m.Name)}Service | EF Core + Npgsql | CRUD for {m.Name} |\n";
        arc += "\n## DataObject\n\n| Entity | Table | Key Fields |\n|---|---|---|\n";
        arc += "| User | Users | Id, Name, Email, PasswordHash |\n";
        foreach (var m in mods) arc += $"| {m.Name} | {H.Plural(H.Pascal(m.Name))} | {string.Join(", ", m.Fields.Take(5).Select(f => f.Name))} |\n";
        arc += "\n## Auth Contract\n- JWT in **httpOnly cookie** `access_token` — never localStorage\n";
        arc += "- Per-tenant: every EF Core query includes `.Where(e => e.UserId == Uid)`\n";
        files["ARCHITECTURE.md"] = arc;

        // openapi.yaml
        var oa  = $"openapi: \"3.1.0\"\ninfo:\n  title: \"{name} API\"\n  version: \"0.1.0\"\npaths:\n";
        oa += "  /api/auth/register:\n    post: {operationId: register, tags: [auth], responses: {201: {description: Created}}}\n";
        oa += "  /api/auth/login:\n    post: {operationId: login, tags: [auth], responses: {200: {description: OK}}}\n";
        oa += "  /api/auth/me:\n    get: {operationId: me, tags: [auth], security: [{cookieAuth: []}], responses: {200: {description: OK}}}\n";
        foreach (var mod in mods)
        {
            var sp = H.Plural(H.Snake(mod.Name)).Replace("_","-");
            var pa = H.Pascal(mod.Name);
            oa += $"  /api/{sp}:\n";
            oa += $"    get:  {{operationId: list{pa},   tags: [{pa}], security: [{{cookieAuth: []}}], responses: {{200: {{description: OK}}}}}}\n";
            oa += $"    post: {{operationId: create{pa}, tags: [{pa}], security: [{{cookieAuth: []}}], responses: {{201: {{description: Created}}}}}}\n";
            oa += $"  /api/{sp}/{{id}}:\n";
            oa += $"    get:    {{operationId: get{pa},    tags: [{pa}], security: [{{cookieAuth: []}}], responses: {{200: {{description: OK}}}}}}\n";
            oa += $"    put:    {{operationId: update{pa}, tags: [{pa}], security: [{{cookieAuth: []}}], responses: {{200: {{description: OK}}}}}}\n";
            oa += $"    delete: {{operationId: delete{pa}, tags: [{pa}], security: [{{cookieAuth: []}}], responses: {{204: {{description: No Content}}}}}}\n";
        }
        oa += "components:\n  securitySchemes:\n    cookieAuth: {type: apiKey, in: cookie, name: access_token}\n";
        files["openapi.yaml"] = oa;

        return files;
    }
}

// ─── Stage 4: PackZip ─────────────────────────────────────────────────────────
static class Stage4
{
    public static byte[] PackZip(Dictionary<string, string> files)
    {
        using var ms  = new MemoryStream();
        using (var zip = new ZipArchive(ms, ZipArchiveMode.Create, leaveOpen: true))
            foreach (var (path, content) in files.OrderBy(kv => kv.Key))
                using (var sw = new StreamWriter(zip.CreateEntry(path, CompressionLevel.Optimal).Open()))
                    sw.Write(content);
        return ms.ToArray();
    }

    public static void WriteDisk(Dictionary<string, string> files, string baseDir)
    {
        foreach (var (path, content) in files)
        {
            var full = Path.Combine(baseDir, path.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(full)!);
            File.WriteAllText(full, content, Encoding.UTF8);
        }
        Console.WriteLine($"Wrote {files.Count} files to {baseDir}");
    }
}

