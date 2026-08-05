package com.archiet.microcodegen;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.SecureRandom;
import java.util.*;
import java.util.regex.*;
import java.util.zip.*;

/**
 * archiet-microcodegen-java v0.1.0
 * PRD text → Spring Boot 3 app → ZIP. Pure Java stdlib. <1400 LOC.
 * Stage 1: Stage1.parsePrd(text)    → Manifest
 * Stage 2: Stage2.toGenome(manifest) → Genome
 * Stage 3: Stage3.renderGenome(genome) → Map<String,String>
 * Stage 4: Stage4.pack(files, path) → ZIP file
 */
public class Main {

    // ─── Domain types ──────────────────────────────────────────────────────────

    record FieldSpec(String type, boolean required, boolean unique) {}

    record EntitySpec(String name, Map<String, FieldSpec> fields, String description) {}

    record UserStory(String asA, String iWant, String soThat) {}

    record Integration(String name, String category) {}

    record Manifest(String solutionName, List<EntitySpec> entities,
                    List<UserStory> userStories, List<Integration> integrations) {}

    record ArchiElement(String name, String type, String description) {}

    record Genome(String genomeVersion, String solutionName, String bundleId, String language,
                  Map<String, EntitySpec> entities, List<UserStory> userStories,
                  List<Integration> integrations, List<ArchiElement> archimateElements) {}

    // ─── String helpers ────────────────────────────────────────────────────────

    static String snake(String s) {
        s = s.replaceAll("([a-z])([A-Z])", "$1_$2");
        s = s.replaceAll("[^a-zA-Z0-9]+", "_").toLowerCase();
        return s.replaceAll("^_+|_+$", "");
    }

    static String camel(String s) {
        String p = pascal(s);
        return p.isEmpty() ? p : Character.toLowerCase(p.charAt(0)) + p.substring(1);
    }

    static String pascal(String s) {
        String[] parts = snake(s).split("_");
        StringBuilder sb = new StringBuilder();
        for (String p : parts) {
            if (!p.isEmpty()) sb.append(Character.toUpperCase(p.charAt(0))).append(p.substring(1));
        }
        return sb.toString();
    }

    static String plural(String s) {
        if (s.endsWith("s")) return s + "es";
        if (s.endsWith("y")) return s.substring(0, s.length() - 1) + "ies";
        return s + "s";
    }

    /** fill("Hello {{NAME}}", Map.of("NAME","World")) → "Hello World" */
    static String fill(String tmpl, Map<String, String> vars) {
        for (Map.Entry<String, String> e : vars.entrySet()) {
            tmpl = tmpl.replace("{{" + e.getKey() + "}}", e.getValue());
        }
        return tmpl;
    }

    static String randomHex(int n) {
        byte[] b = new byte[n];
        new SecureRandom().nextBytes(b);
        StringBuilder sb = new StringBuilder();
        for (byte x : b) sb.append(String.format("%02x", x & 0xff));
        return sb.toString();
    }

    // ─── STAGE 1 ───────────────────────────────────────────────────────────────

    static class Stage1 {
        static final Pattern RE_SECTION   = Pattern.compile("(?im)^#{1,3}\\s*(?:entities|data models|domain models|entity list)\\s*:?\\s*$");
        static final Pattern RE_ENT_NAME  = Pattern.compile("(?m)^[\\s\\-\\*\\#]+\\*{0,2}([A-Z][a-zA-Z0-9_]{1,40})\\*{0,2}[ \\t]*(?::|—|-|[ \\t]|$)");
        static final Pattern RE_FIELD     = Pattern.compile("(?m)^[\\s\\-\\*]+([a-z_][a-z0-9_]{0,40})\\s*[:\\-]\\s*([a-zA-Z]+)([^\\n]*)");
        static final Pattern RE_INL       = Pattern.compile("([a-z_][a-z0-9_]{0,40})\\s*\\(\\s*([a-zA-Z]+)([^)]*)");
        static final Pattern RE_STORY     = Pattern.compile("(?im)As\\s+(?:a|an)\\s+([^,]+?),\\s+I\\s+want\\s+(?:to\\s+)?([^,]+?)(?:,?\\s*so\\s+that\\s+([^.]+))?\\.\\s*$");
        static final Pattern RE_TITLE     = Pattern.compile("(?m)^#\\s+(.+?)\\s*$");

        static final Map<String, String> INTEGRATIONS = Map.of(
            "stripe", "payments", "auth0", "auth", "clerk", "auth",
            "sendgrid", "email", "twilio", "sms", "datadog", "observability"
        );

        static Manifest parsePrd(String text) {
            String solutionName = "Generated App";
            Matcher m = RE_TITLE.matcher(text);
            if (m.find()) solutionName = m.group(1);

            String entitySection = "";
            Matcher secM = RE_SECTION.matcher(text);
            if (secM.find()) {
                String rest = text.substring(secM.end());
                Matcher nextH = Pattern.compile("(?m)^#{1,2}\\s+\\S").matcher(rest);
                entitySection = nextH.find() ? rest.substring(0, nextH.start()) : rest;
            }

            Set<String> seen = new LinkedHashSet<>();
            List<EntitySpec> entities = new ArrayList<>();
            Matcher em = RE_ENT_NAME.matcher(entitySection);
            List<int[]> entMatches = new ArrayList<>();
            while (em.find()) entMatches.add(new int[]{em.start(), em.end(), em.start(2), em.end(2)});

            for (int i = 0; i < entMatches.size(); i++) {
                int[] cur = entMatches.get(i);
                String ename = entitySection.substring(cur[2], cur[3]);
                if (seen.contains(ename)) continue;
                seen.add(ename);
                int bodyEnd = i + 1 < entMatches.size() ? entMatches.get(i + 1)[0] : entitySection.length();
                String body = entitySection.substring(cur[1], bodyEnd);

                Map<String, FieldSpec> fields = new LinkedHashMap<>();
                Set<String> seenF = new HashSet<>();
                Matcher fm = RE_FIELD.matcher(body);
                while (fm.find()) {
                    String fn = fm.group(1), ft = fm.group(2).toLowerCase(), mod = fm.group(3).toLowerCase();
                    if (!seenF.contains(fn)) {
                        seenF.add(fn);
                        fields.put(fn, new FieldSpec(ft, mod.contains("required") || mod.contains("not null"), mod.contains("unique")));
                    }
                }
                if (fields.isEmpty()) {
                    String line = entitySection.substring(cur[0], cur[1]) + body.split("\n", 2)[0];
                    Matcher im = RE_INL.matcher(line);
                    while (im.find()) {
                        String fn = im.group(1), ft = im.group(2).toLowerCase(), mod = im.group(3).toLowerCase();
                        if (!seenF.contains(fn)) {
                            seenF.add(fn);
                            fields.put(fn, new FieldSpec(ft, mod.contains("required"), false));
                        }
                    }
                }
                entities.add(new EntitySpec(ename, fields, ename + " entity"));
            }

            List<UserStory> stories = new ArrayList<>();
            Matcher sm = RE_STORY.matcher(text);
            while (sm.find()) stories.add(new UserStory(sm.group(1).trim(), sm.group(2).trim(), sm.group(3) != null ? sm.group(3).trim() : ""));

            String low = text.toLowerCase();
            List<Integration> integrations = new ArrayList<>();
            INTEGRATIONS.forEach((k, v) -> { if (low.contains(k)) integrations.add(new Integration(k, v)); });

            return new Manifest(solutionName, entities, stories, integrations);
        }
    }

    // ─── STAGE 2 ───────────────────────────────────────────────────────────────

    static class Stage2 {
        static Genome toGenome(Manifest manifest) {
            Map<String, EntitySpec> entMap = new LinkedHashMap<>();
            for (EntitySpec ent : manifest.entities()) {
                Map<String, FieldSpec> fields = new LinkedHashMap<>();
                fields.put("id", new FieldSpec("uuid", true, false));
                ent.fields().forEach((k, v) -> { if (!k.equals("id") && !k.equals("created_at") && !k.equals("updated_at")) fields.put(k, v); });
                entMap.put(ent.name(), new EntitySpec(ent.name(), fields, ent.name() + " entity (generated)"));
            }
            List<ArchiElement> elements = new ArrayList<>();
            elements.add(new ArchiElement(manifest.solutionName(), "ApplicationComponent", manifest.solutionName() + " Spring Boot application"));
            for (EntitySpec e : manifest.entities()) elements.add(new ArchiElement(e.name(), "DataObject", e.name() + " entity"));
            for (Integration i : manifest.integrations()) elements.add(new ArchiElement(i.name(), "ApplicationService", "External: " + i.name()));
            return new Genome("1.0.0", manifest.solutionName(), snake(manifest.solutionName()), "java-spring-boot",
                entMap, manifest.userStories(), manifest.integrations(), elements);
        }
    }

    // ─── STAGE 3 ───────────────────────────────────────────────────────────────

    static class Stage3 {
        static final Map<String, String> JPA_TYPES = Map.ofEntries(
            Map.entry("string", "String"), Map.entry("text", "String"),
            Map.entry("integer", "Long"), Map.entry("int", "Long"),
            Map.entry("float", "Double"), Map.entry("decimal", "java.math.BigDecimal"),
            Map.entry("boolean", "Boolean"), Map.entry("bool", "Boolean"),
            Map.entry("datetime", "java.time.LocalDateTime"),
            Map.entry("date", "java.time.LocalDate"),
            Map.entry("uuid", "String"), Map.entry("json", "String")
        );
        static final Map<String, String> COL_DEF = Map.ofEntries(
            Map.entry("string", "VARCHAR(255)"), Map.entry("text", "TEXT"),
            Map.entry("integer", "BIGINT"), Map.entry("int", "BIGINT"),
            Map.entry("float", "DOUBLE PRECISION"), Map.entry("decimal", "NUMERIC(18,2)"),
            Map.entry("boolean", "BOOLEAN"), Map.entry("bool", "BOOLEAN"),
            Map.entry("datetime", "TIMESTAMP"), Map.entry("date", "DATE"),
            Map.entry("uuid", "VARCHAR(36)"), Map.entry("json", "TEXT")
        );

        static String jpaType(String t) { return JPA_TYPES.getOrDefault(t, "String"); }
        static String colDef(String t)  { return COL_DEF.getOrDefault(t, "VARCHAR(255)"); }

        static String fieldDecl(String fname, FieldSpec fs) {
            String jt = jpaType(fs.type());
            String cd = colDef(fs.type());
            String ann = fs.required() ? "    @Column(name = \"" + fname + "\", columnDefinition = \"" + cd + "\", nullable = false)" :
                                          "    @Column(name = \"" + fname + "\", columnDefinition = \"" + cd + "\")";
            return ann + "\n    private " + jt + " " + camelField(fname) + ";";
        }

        static String camelField(String s) { return camel(s); }

        static Map<String, String> renderGenome(Genome g) {
            Map<String, String> files = new LinkedHashMap<>();
            String bundleId  = g.bundleId();
            String jwtSecret = randomHex(24);
            String pkg       = "com.example." + bundleId.replace("-", "");
            String pkgDir    = "src/main/java/" + pkg.replace(".", "/");

            // Application entry point
            files.put(pkgDir + "/Application.java", "package " + pkg + ";\n\nimport org.springframework.boot.SpringApplication;\nimport org.springframework.boot.autoconfigure.SpringBootApplication;\n\n@SpringBootApplication\npublic class Application {\n    public static void main(String[] args) {\n        SpringApplication.run(Application.class, args);\n    }\n}\n");

            // User model
            files.put(pkgDir + "/model/User.java", renderUserModel(pkg));

            // Auth
            files.put(pkgDir + "/security/JwtFilter.java", renderJwtFilter(pkg, jwtSecret));
            files.put(pkgDir + "/security/SecurityConfig.java", renderSecurityConfig(pkg));
            files.put(pkgDir + "/repository/UserRepository.java", "package " + pkg + ".repository;\nimport " + pkg + ".model.User;\nimport org.springframework.data.jpa.repository.JpaRepository;\nimport java.util.Optional;\npublic interface UserRepository extends JpaRepository<User, String> {\n    Optional<User> findByEmail(String email);\n}\n");
            files.put(pkgDir + "/service/AuthService.java", renderAuthService(pkg, jwtSecret));
            files.put(pkgDir + "/controller/AuthController.java", renderAuthController(pkg));

            // Per-entity files
            for (Map.Entry<String, EntitySpec> entry : g.entities().entrySet()) {
                String eName = entry.getKey();
                EntitySpec eSpec = entry.getValue();
                String eSnake  = snake(eName);
                String ePascal = pascal(eName);
                String ePlural = plural(eSnake);

                StringBuilder fieldDecls = new StringBuilder();
                StringBuilder getSetters  = new StringBuilder();
                for (Map.Entry<String, FieldSpec> fe : eSpec.fields().entrySet()) {
                    if (fe.getKey().equals("id")) continue;
                    fieldDecls.append("\n").append(fieldDecl(fe.getKey(), fe.getValue())).append("\n");
                    String cf = camelField(fe.getKey());
                    String jt = jpaType(fe.getValue().type());
                    getSetters.append("    public ").append(jt).append(" get").append(pascal(fe.getKey())).append("() { return ").append(cf).append("; }\n");
                    getSetters.append("    public void set").append(pascal(fe.getKey())).append("(").append(jt).append(" ").append(cf).append(") { this.").append(cf).append(" = ").append(cf).append("; }\n");
                }

                files.put(pkgDir + "/model/" + ePascal + ".java", renderEntityModel(pkg, ePascal, ePlural, fieldDecls.toString(), getSetters.toString()));
                files.put(pkgDir + "/repository/" + ePascal + "Repository.java", renderRepository(pkg, ePascal));
                files.put(pkgDir + "/service/" + ePascal + "Service.java", renderService(pkg, ePascal));
                files.put(pkgDir + "/controller/" + ePascal + "Controller.java", renderController(pkg, ePascal, ePlural));
            }

            // application.properties
            files.put("src/main/resources/application.properties",
                "spring.datasource.url=${DATABASE_URL:jdbc:postgresql://localhost:5432/" + bundleId + "}\n" +
                "spring.datasource.driver-class-name=org.postgresql.Driver\n" +
                "spring.jpa.hibernate.ddl-auto=update\n" +
                "spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect\n" +
                "jwt.secret=${JWT_SECRET_KEY:" + jwtSecret + "}\n" +
                "server.port=${PORT:8080}\n");

            // pom.xml for generated app
            files.put("pom.xml", renderAppPom(bundleId));

            // docker-compose
            files.put("docker-compose.yml",
                "services:\n  app:\n    build: .\n    ports: [\"8080:8080\"]\n    environment:\n" +
                "      DATABASE_URL: jdbc:postgresql://db:5432/" + bundleId + "\n" +
                "      JWT_SECRET_KEY: " + jwtSecret + "\n" +
                "    depends_on:\n      db:\n        condition: service_healthy\n" +
                "  db:\n    image: postgres:16\n    environment:\n" +
                "      POSTGRES_USER: archiet\n      POSTGRES_PASSWORD: archiet\n      POSTGRES_DB: " + bundleId + "\n" +
                "    volumes: [\"pgdata:/var/lib/postgresql/data\"]\n" +
                "    healthcheck:\n      test: [\"CMD-SHELL\",\"pg_isready -U archiet -d " + bundleId + "\"]\n" +
                "      interval: 3s\n      timeout: 3s\n      retries: 20\nvolumes:\n  pgdata:\n");

            files.put("Dockerfile",
                "FROM eclipse-temurin:17-jdk-alpine AS builder\nWORKDIR /app\nCOPY pom.xml .\nCOPY src ./src\nRUN apk add --no-cache maven && mvn package -q -DskipTests\n\n" +
                "FROM eclipse-temurin:17-jre-alpine\nWORKDIR /app\nCOPY --from=builder /app/target/*.jar app.jar\nEXPOSE 8080\nCMD [\"java\",\"-jar\",\"app.jar\"]\n");

            files.put(".env.example",
                "DATABASE_URL=jdbc:postgresql://localhost:5432/" + bundleId + "\nJWT_SECRET_KEY=" + jwtSecret + "\nPORT=8080\n");

            files.put("ARCHITECTURE.md", renderArchMd(g));
            files.put("openapi.yaml",    renderOpenapi(g));
            files.put("README.md",       renderReadme(g));
            return files;
        }

        static String renderUserModel(String pkg) {
            return "package " + pkg + ".model;\n\nimport jakarta.persistence.*;\nimport java.time.LocalDateTime;\n\n@Entity\n@Table(name = \"users\")\npublic class User {\n    @Id\n    @GeneratedValue(strategy = GenerationType.UUID)\n    private String id;\n\n    @Column(nullable = false, unique = true)\n    private String email;\n\n    @Column(name = \"password_hash\", nullable = false)\n    private String passwordHash;\n\n    @Column(name = \"created_at\")\n    private LocalDateTime createdAt;\n\n    @PrePersist\n    protected void onCreate() { createdAt = LocalDateTime.now(); }\n\n    public String getId() { return id; }\n    public void setId(String id) { this.id = id; }\n    public String getEmail() { return email; }\n    public void setEmail(String email) { this.email = email; }\n    public String getPasswordHash() { return passwordHash; }\n    public void setPasswordHash(String h) { this.passwordHash = h; }\n    public LocalDateTime getCreatedAt() { return createdAt; }\n}\n";
        }

        static String renderJwtFilter(String pkg, String jwtSecret) {
            return "package " + pkg + ".security;\n\nimport jakarta.servlet.*;\nimport jakarta.servlet.http.*;\nimport org.springframework.security.authentication.UsernamePasswordAuthenticationToken;\nimport org.springframework.security.core.context.SecurityContextHolder;\nimport org.springframework.web.filter.OncePerRequestFilter;\nimport javax.crypto.spec.SecretKeySpec;\nimport java.io.IOException;\nimport java.nio.charset.StandardCharsets;\nimport java.util.Base64;\n\n/** Reads JWT from httpOnly cookie -- never from Authorization header body or localStorage. */\npublic class JwtFilter extends OncePerRequestFilter {\n    private final String secret;\n    public JwtFilter(String secret) { this.secret = secret; }\n\n    @Override\n    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)\n            throws ServletException, IOException {\n        String token = null;\n        if (req.getCookies() != null) {\n            for (Cookie c : req.getCookies()) {\n                if (\"access_token\".equals(c.getName())) { token = c.getValue(); break; }\n            }\n        }\n        if (token != null) {\n            try {\n                String[] parts = token.split(\"\\\\.\");\n                if (parts.length == 3) {\n                    String payload = new String(Base64.getUrlDecoder().decode(parts[1]), StandardCharsets.UTF_8);\n                    String sub = payload.replaceAll(\".*\\\"sub\\\":\\\"([^\\\"]+)\\\".*\", \"$1\");\n                    if (!sub.equals(payload)) {\n                        UsernamePasswordAuthenticationToken auth = new UsernamePasswordAuthenticationToken(sub, null, java.util.List.of());\n                        SecurityContextHolder.getContext().setAuthentication(auth);\n                    }\n                }\n            } catch (Exception ignored) {}\n        }\n        chain.doFilter(req, res);\n    }\n}\n";
        }

        static String renderSecurityConfig(String pkg) {
            return "package " + pkg + ".security;\n\nimport org.springframework.beans.factory.annotation.Autowired;\nimport org.springframework.beans.factory.annotation.Value;\nimport org.springframework.context.annotation.Bean;\nimport org.springframework.context.annotation.Configuration;\nimport org.springframework.http.HttpMethod;\nimport org.springframework.security.config.annotation.web.builders.HttpSecurity;\nimport org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;\nimport org.springframework.security.config.http.SessionCreationPolicy;\nimport org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;\nimport org.springframework.security.web.SecurityFilterChain;\nimport org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;\n\n@Configuration\n@EnableWebSecurity\npublic class SecurityConfig {\n    @Value(\"${jwt.secret}\")\n    private String jwtSecret;\n\n    @Bean\n    public BCryptPasswordEncoder passwordEncoder() { return new BCryptPasswordEncoder(); }\n\n    @Bean\n    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {\n        http.csrf(c -> c.disable())\n            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))\n            .authorizeHttpRequests(a -> a\n                .requestMatchers(\"/auth/**\").permitAll()\n                .anyRequest().authenticated())\n            .addFilterBefore(new JwtFilter(jwtSecret), UsernamePasswordAuthenticationFilter.class);\n        return http.build();\n    }\n}\n";
        }

        static String renderAuthService(String pkg, String jwtSecret) {
            return "package " + pkg + ".service;\n\nimport " + pkg + ".model.User;\nimport " + pkg + ".repository.UserRepository;\nimport org.springframework.beans.factory.annotation.Autowired;\nimport org.springframework.beans.factory.annotation.Value;\nimport org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;\nimport org.springframework.stereotype.Service;\nimport javax.crypto.Mac;\nimport javax.crypto.spec.SecretKeySpec;\nimport java.nio.charset.StandardCharsets;\nimport java.util.Base64;\n\n@Service\npublic class AuthService {\n    @Autowired UserRepository users;\n    @Autowired BCryptPasswordEncoder encoder;\n    @Value(\"${jwt.secret}\") String secret;\n\n    public String register(String email, String password) {\n        if (users.findByEmail(email.toLowerCase()).isPresent()) throw new RuntimeException(\"EMAIL_TAKEN\");\n        User u = new User();\n        u.setEmail(email.toLowerCase());\n        u.setPasswordHash(encoder.encode(password));\n        users.save(u);\n        return generateJwt(u.getId(), u.getEmail());\n    }\n\n    public String login(String email, String password) {\n        User u = users.findByEmail(email.toLowerCase()).orElseThrow(() -> new RuntimeException(\"INVALID_CREDENTIALS\"));\n        if (!encoder.matches(password, u.getPasswordHash())) throw new RuntimeException(\"INVALID_CREDENTIALS\");\n        return generateJwt(u.getId(), u.getEmail());\n    }\n\n    private String generateJwt(String sub, String email) {\n        try {\n            String header = Base64.getUrlEncoder().withoutPadding().encodeToString(\"{\\\"alg\\\":\\\"HS256\\\",\\\"typ\\\":\\\"JWT\\\"}\".getBytes(StandardCharsets.UTF_8));\n            long exp = System.currentTimeMillis() / 1000 + 7 * 86400;\n            String body = Base64.getUrlEncoder().withoutPadding().encodeToString(('{' + \"\\\"sub\\\":\\\"\" + sub + \"\\\",\\\"email\\\":\\\"\" + email + \"\\\",\\\"exp\\\":\" + exp + '}').getBytes(StandardCharsets.UTF_8));\n            String sig = sign(header + \".\" + body);\n            return header + \".\" + body + \".\" + sig;\n        } catch (Exception e) { throw new RuntimeException(e); }\n    }\n\n    private String sign(String data) throws Exception {\n        Mac mac = Mac.getInstance(\"HmacSHA256\");\n        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), \"HmacSHA256\"));\n        return Base64.getUrlEncoder().withoutPadding().encodeToString(mac.doFinal(data.getBytes(StandardCharsets.UTF_8)));\n    }\n}\n";
        }

        static String renderAuthController(String pkg) {
            return "package " + pkg + ".controller;\n\nimport " + pkg + ".service.AuthService;\nimport jakarta.servlet.http.*;\nimport org.springframework.beans.factory.annotation.Autowired;\nimport org.springframework.http.*;\nimport org.springframework.security.core.context.SecurityContextHolder;\nimport org.springframework.web.bind.annotation.*;\nimport java.util.Map;\n\n@RestController\n@RequestMapping(\"/auth\")\npublic class AuthController {\n    @Autowired AuthService authService;\n\n    @PostMapping(\"/register\")\n    public ResponseEntity<?> register(@RequestBody Map<String,String> body, HttpServletResponse res) {\n        try {\n            String token = authService.register(body.get(\"email\"), body.get(\"password\"));\n            // JWT set as httpOnly cookie — never returned in body or localStorage.\n            res.addCookie(jwtCookie(token));\n            return ResponseEntity.status(HttpStatus.CREATED).body(Map.of(\"message\", \"registered\"));\n        } catch (RuntimeException e) {\n            if (\"EMAIL_TAKEN\".equals(e.getMessage())) return ResponseEntity.status(409).body(Map.of(\"error\", \"email_taken\", \"message\", \"Email already registered.\"));\n            return ResponseEntity.status(500).body(Map.of(\"error\", \"server_error\", \"message\", e.getMessage()));\n        }\n    }\n\n    @PostMapping(\"/login\")\n    public ResponseEntity<?> login(@RequestBody Map<String,String> body, HttpServletResponse res) {\n        try {\n            String token = authService.login(body.get(\"email\"), body.get(\"password\"));\n            res.addCookie(jwtCookie(token));\n            return ResponseEntity.ok(Map.of(\"message\", \"logged in\"));\n        } catch (RuntimeException e) {\n            return ResponseEntity.status(401).body(Map.of(\"error\", \"invalid_credentials\", \"message\", \"Invalid credentials.\"));\n        }\n    }\n\n    @PostMapping(\"/logout\")\n    public ResponseEntity<?> logout(HttpServletResponse res) {\n        Cookie c = new Cookie(\"access_token\", \"\"); c.setMaxAge(0); c.setPath(\"/\"); res.addCookie(c);\n        return ResponseEntity.ok(Map.of(\"message\", \"logged out\"));\n    }\n\n    @GetMapping(\"/me\")\n    public ResponseEntity<?> me() {\n        String id = SecurityContextHolder.getContext().getAuthentication().getName();\n        return ResponseEntity.ok(Map.of(\"id\", id));\n    }\n\n    private Cookie jwtCookie(String token) {\n        Cookie c = new Cookie(\"access_token\", token);\n        c.setHttpOnly(true);\n        c.setPath(\"/\");\n        c.setMaxAge(7 * 86400);\n        return c;\n    }\n}\n";
        }

        static String renderEntityModel(String pkg, String ePascal, String ePlural, String fields, String getSetters) {
            return "package " + pkg + ".model;\n\nimport jakarta.persistence.*;\nimport java.time.*;\n\n// Per-tenant entity: userId scopes every record to its owner.\n// Every repository method filters by userId — no cross-user leaks.\n@Entity\n@Table(name = \"" + ePlural + "\")\npublic class " + ePascal + " {\n    @Id\n    @GeneratedValue(strategy = GenerationType.UUID)\n    private String id;\n\n    @Column(name = \"user_id\", nullable = false)\n    private String userId;\n\n" + fields + "\n    @Column(name = \"created_at\")\n    private LocalDateTime createdAt;\n\n    @Column(name = \"updated_at\")\n    private LocalDateTime updatedAt;\n\n    @PrePersist\n    protected void onCreate() { createdAt = updatedAt = LocalDateTime.now(); }\n\n    @PreUpdate\n    protected void onUpdate() { updatedAt = LocalDateTime.now(); }\n\n    public String getId() { return id; }\n    public void setId(String id) { this.id = id; }\n    public String getUserId() { return userId; }\n    public void setUserId(String userId) { this.userId = userId; }\n" + getSetters +
                   "    public LocalDateTime getCreatedAt() { return createdAt; }\n    public LocalDateTime getUpdatedAt() { return updatedAt; }\n}\n";
        }

        static String renderRepository(String pkg, String ePascal) {
            return "package " + pkg + ".repository;\n\nimport " + pkg + ".model." + ePascal + ";\nimport org.springframework.data.jpa.repository.JpaRepository;\nimport java.util.*;\n\npublic interface " + ePascal + "Repository extends JpaRepository<" + ePascal + ", String> {\n    List<" + ePascal + "> findAllByUserId(String userId);\n    Optional<" + ePascal + "> findByIdAndUserId(String id, String userId);\n}\n";
        }

        static String renderService(String pkg, String ePascal) {
            return "package " + pkg + ".service;\n\nimport " + pkg + ".model." + ePascal + ";\nimport " + pkg + ".repository." + ePascal + "Repository;\nimport org.springframework.beans.factory.annotation.Autowired;\nimport org.springframework.stereotype.Service;\nimport org.springframework.transaction.annotation.Transactional;\nimport java.util.*;\n\n@Service\npublic class " + ePascal + "Service {\n    @Autowired " + ePascal + "Repository repo;\n\n    public List<" + ePascal + "> findAll(String userId) { return repo.findAllByUserId(userId); }\n\n    public " + ePascal + " findOne(String id, String userId) {\n        return repo.findByIdAndUserId(id, userId).orElseThrow(() -> new NoSuchElementException(\"Not found\"));\n    }\n\n    @Transactional\n    public " + ePascal + " create(" + ePascal + " item, String userId) {\n        item.setUserId(userId);\n        return repo.save(item);\n    }\n\n    @Transactional\n    public " + ePascal + " update(String id, " + ePascal + " updates, String userId) {\n        " + ePascal + " item = findOne(id, userId);\n        updates.setId(id);\n        updates.setUserId(userId);\n        return repo.save(updates);\n    }\n\n    @Transactional\n    public void delete(String id, String userId) { " + ePascal + " item = findOne(id, userId); repo.delete(item); }\n}\n";
        }

        static String renderController(String pkg, String ePascal, String ePlural) {
            return "package " + pkg + ".controller;\n\nimport " + pkg + ".model." + ePascal + ";\nimport " + pkg + ".service." + ePascal + "Service;\nimport org.springframework.beans.factory.annotation.Autowired;\nimport org.springframework.http.*;\nimport org.springframework.security.core.context.SecurityContextHolder;\nimport org.springframework.web.bind.annotation.*;\nimport java.util.*;\n\n@RestController\n@RequestMapping(\"/" + ePlural + "\")\npublic class " + ePascal + "Controller {\n    @Autowired " + ePascal + "Service svc;\n\n    private String userId() { return SecurityContextHolder.getContext().getAuthentication().getName(); }\n\n    @GetMapping\n    public ResponseEntity<List<" + ePascal + ">> list() { return ResponseEntity.ok(svc.findAll(userId())); }\n\n    @PostMapping\n    public ResponseEntity<" + ePascal + "> create(@RequestBody " + ePascal + " item) {\n        return ResponseEntity.status(HttpStatus.CREATED).body(svc.create(item, userId()));\n    }\n\n    @GetMapping(\"/{id}\")\n    public ResponseEntity<?> get(@PathVariable String id) {\n        try { return ResponseEntity.ok(svc.findOne(id, userId())); }\n        catch (NoSuchElementException e) { return ResponseEntity.notFound().build(); }\n    }\n\n    @PutMapping(\"/{id}\")\n    public ResponseEntity<?> update(@PathVariable String id, @RequestBody " + ePascal + " item) {\n        try { return ResponseEntity.ok(svc.update(id, item, userId())); }\n        catch (NoSuchElementException e) { return ResponseEntity.notFound().build(); }\n    }\n\n    @DeleteMapping(\"/{id}\")\n    public ResponseEntity<Void> delete(@PathVariable String id) {\n        try { svc.delete(id, userId()); return ResponseEntity.noContent().build(); }\n        catch (NoSuchElementException e) { return ResponseEntity.notFound().build(); }\n    }\n}\n";
        }

        static String renderAppPom(String bundleId) {
            return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<project xmlns=\"http://maven.apache.org/POM/4.0.0\"\n         xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"\n         xsi:schemaLocation=\"http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd\">\n    <modelVersion>4.0.0</modelVersion>\n    <parent>\n        <groupId>org.springframework.boot</groupId>\n        <artifactId>spring-boot-starter-parent</artifactId>\n        <version>3.2.3</version>\n    </parent>\n    <groupId>com.example</groupId>\n    <artifactId>" + bundleId + "</artifactId>\n    <version>0.1.0</version>\n    <properties>\n        <java.version>17</java.version>\n    </properties>\n    <dependencies>\n        <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>\n        <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>\n        <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-security</artifactId></dependency>\n        <dependency><groupId>org.postgresql</groupId><artifactId>postgresql</artifactId><scope>runtime</scope></dependency>\n    </dependencies>\n    <build>\n        <plugins>\n            <plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin>\n        </plugins>\n    </build>\n</project>\n";
        }

        static String renderArchMd(Genome g) {
            StringBuilder sb = new StringBuilder();
            sb.append("# Architecture — ").append(g.solutionName()).append("\n\nGenerated by archiet-microcodegen-java · ArchiMate 3.2\n\n");
            sb.append("## Application Layer\n\n| Element | Type | Description |\n|---------|------|-------------|\n");
            for (ArchiElement el : g.archimateElements()) sb.append("| `").append(el.name()).append("` | ").append(el.type()).append(" | ").append(el.description()).append(" |\n");
            sb.append("\n## Relationships\n\n```\n  ").append(g.solutionName()).append(" (ApplicationComponent)\n");
            for (String en : g.entities().keySet()) sb.append("    └── ").append(en).append(" (DataObject)  [Realization]\n");
            sb.append("```\n\nhttps://archiet.com?utm_source=maven&utm_medium=package&utm_campaign=microcodegen-java\n");
            return sb.toString();
        }

        static String renderOpenapi(Genome g) {
            StringBuilder sb = new StringBuilder();
            sb.append("openapi: '3.1.0'\ninfo:\n  title: ").append(g.solutionName()).append(" API\n  version: 0.1.0\nservers:\n  - url: http://localhost:8080\npaths:\n");
            sb.append("  /auth/register:\n    post:\n      tags: [auth]\n      summary: Register (returns httpOnly JWT cookie)\n      responses: {'201': {description: Registered}}\n");
            sb.append("  /auth/login:\n    post:\n      tags: [auth]\n      summary: Login (returns httpOnly JWT cookie)\n      responses: {'200': {description: OK}}\n");
            for (Map.Entry<String, EntitySpec> e : g.entities().entrySet()) {
                String eName = e.getKey(), ePlural = plural(snake(eName));
                sb.append("  /").append(ePlural).append(":\n    get:\n      tags: [").append(eName).append("]\n      security: [{cookieAuth: []}]\n      responses: {'200': {description: List}}\n");
                sb.append("    post:\n      tags: [").append(eName).append("]\n      security: [{cookieAuth: []}]\n      responses: {'201': {description: Created}}\n");
                sb.append("  /").append(ePlural).append("/{id}:\n    get:\n      tags: [").append(eName).append("]\n      security: [{cookieAuth: []}]\n      parameters: [{in: path, name: id, required: true, schema: {type: string}}]\n      responses: {'200': {description: OK}, '404': {description: Not found}}\n");
                sb.append("    put:\n      tags: [").append(eName).append("]\n      security: [{cookieAuth: []}]\n      parameters: [{in: path, name: id, required: true, schema: {type: string}}]\n      responses: {'200': {description: Updated}}\n");
                sb.append("    delete:\n      tags: [").append(eName).append("]\n      security: [{cookieAuth: []}]\n      parameters: [{in: path, name: id, required: true, schema: {type: string}}]\n      responses: {'204': {description: Deleted}}\n");
            }
            sb.append("components:\n  securitySchemes:\n    cookieAuth:\n      type: apiKey\n      in: cookie\n      name: access_token\n");
            return sb.toString();
        }

        static String renderReadme(Genome g) {
            return "# " + g.solutionName() + "\n\nGenerated by archiet-microcodegen-java.\n\n## Quick start\n\n```bash\ncp .env.example .env\ndocker compose up\n```\n\n## Stack\n\n- Spring Boot 3.2 + Spring Security\n- JPA + PostgreSQL 16\n- JWT httpOnly cookies (never localStorage)\n- Per-tenant: every query filtered by userId\n";
        }
    }

    // ─── STAGE 4 ───────────────────────────────────────────────────────────────

    static class Stage4 {
        static void pack(Map<String, String> files, String zipPath) throws IOException {
            try (ZipOutputStream zos = new ZipOutputStream(new FileOutputStream(zipPath))) {
                for (Map.Entry<String, String> e : files.entrySet()) {
                    zos.putNextEntry(new ZipEntry(e.getKey()));
                    zos.write(e.getValue().getBytes(StandardCharsets.UTF_8));
                    zos.closeEntry();
                }
            }
        }

        static void writeDir(Map<String, String> files, String outDir) throws IOException {
            for (Map.Entry<String, String> e : files.entrySet()) {
                Path p = Paths.get(outDir, e.getKey());
                Files.createDirectories(p.getParent());
                Files.writeString(p, e.getValue(), StandardCharsets.UTF_8);
            }
        }
    }

    // ─── CLI ──────────────────────────────────────────────────────────────────

    public static void main(String[] args) throws Exception {
        if (args.length == 0 || args[0].equals("--help") || args[0].equals("-h")) {
            System.err.println("archiet-microcodegen-java — PRD text → Spring Boot 3 app\n");
            System.err.println("Usage:");
            System.err.println("  java -jar archiet-microcodegen-java.jar prd.md --out ./myapp/");
            System.err.println("  java -jar archiet-microcodegen-java.jar prd.md --zip myapp.zip");
            return;
        }

        String prdPath = args[0];
        String outDir  = null, zipPath = null;
        for (int i = 1; i < args.length; i++) {
            if ("--out".equals(args[i]) && i + 1 < args.length)  { outDir  = args[++i]; }
            if ("--zip".equals(args[i]) && i + 1 < args.length)  { zipPath = args[++i]; }
        }

        String text     = Files.readString(Paths.get(prdPath), StandardCharsets.UTF_8);
        Manifest m      = Stage1.parsePrd(text);
        Genome g        = Stage2.toGenome(m);
        Map<String, String> files = Stage3.renderGenome(g);

        if (outDir != null) {
            Stage4.writeDir(files, outDir);
            System.err.println("Wrote " + files.size() + " files to " + outDir);
        } else {
            String out = zipPath != null ? zipPath : g.bundleId() + ".zip";
            Stage4.pack(files, out);
            System.err.println("Wrote " + out);
        }
    }
}
