// archiet-microcodegen-go v0.1.0
// PRD text → Go chi app → ZIP. Pure Go stdlib. <1400 LOC.
// Stage 1: ParsePRD(text)            → Manifest (language-agnostic)
// Stage 2: ManifestToGenome(manifest) → Genome  (ArchiMate 3.2 typed)
// Stage 3: RenderGenome(genome)      → map[string]string (Go chi-specific)
// Stage 4: Pack(files)               → []byte (ZIP) or write to disk
// Zero external imports. Inspired by Karpathy's micrograd.
package main

import (
	"archive/zip"
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// ─── Domain types ─────────────────────────────────────────────────────────────

type FieldSpec struct {
	Type     string `json:"type"`
	Required bool   `json:"required"`
	Unique   bool   `json:"unique"`
	Indexed  bool   `json:"indexed"`
}

type Entity struct {
	Name        string               `json:"name"`
	Fields      map[string]FieldSpec `json:"fields"`
	Description string               `json:"description"`
}

type UserStory struct {
	AsA     string `json:"as_a"`
	IWant   string `json:"i_want"`
	SoThat  string `json:"so_that"`
}

type Integration struct {
	Name     string `json:"name"`
	Category string `json:"category"`
}

type Manifest struct {
	SolutionName string        `json:"solution_name"`
	Entities     []Entity      `json:"entities"`
	UserStories  []UserStory   `json:"user_stories"`
	Integrations []Integration `json:"integrations"`
}

type ArchiMateElement struct {
	Name        string `json:"name"`
	Type        string `json:"type"`
	Description string `json:"description"`
}

type CoreModule struct {
	ModuleType string            `json:"module_type"`
	Entities   map[string]Entity `json:"entities"`
}

type Modules struct {
	Core CoreModule `json:"core"`
}

type Genome struct {
	GenomeVersion     string             `json:"genome_version"`
	SolutionName      string             `json:"solution_name"`
	BundleID          string             `json:"bundle_id"`
	Language          string             `json:"language"`
	Modules           Modules            `json:"modules"`
	UserStories       []UserStory        `json:"user_stories"`
	Integrations      []Integration      `json:"integrations"`
	ArchiMateElements []ArchiMateElement `json:"archimate_elements"`
}

// ─── String helpers ───────────────────────────────────────────────────────────

var (
	reNotAlnum  = regexp.MustCompile(`[^a-zA-Z0-9]+`)
	reCamelSep  = regexp.MustCompile(`([a-z])([A-Z])`)
)

func snake(s string) string {
	s = reCamelSep.ReplaceAllString(s, "${1}_${2}")
	s = reNotAlnum.ReplaceAllString(s, "_")
	return strings.ToLower(strings.Trim(s, "_"))
}

func pascal(s string) string {
	parts := strings.Split(snake(s), "_")
	sb := strings.Builder{}
	for _, p := range parts {
		if len(p) == 0 {
			continue
		}
		sb.WriteString(strings.ToUpper(p[:1]) + p[1:])
	}
	return sb.String()
}

func camel(s string) string {
	p := pascal(s)
	if len(p) == 0 {
		return p
	}
	return strings.ToLower(p[:1]) + p[1:]
}

func plural(s string) string {
	if strings.HasSuffix(s, "s") {
		return s + "es"
	}
	if strings.HasSuffix(s, "y") {
		return s[:len(s)-1] + "ies"
	}
	return s + "s"
}

// fill replaces {{VAR}} placeholders in template with values from vars map.
func fill(tmpl string, vars map[string]string) string {
	result := tmpl
	for k, v := range vars {
		result = strings.ReplaceAll(result, "{{"+k+"}}", v)
	}
	return result
}

func randomHex(n int) string {
	b := make([]byte, n)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// ─── Type maps ────────────────────────────────────────────────────────────────

var goGormTypes = map[string]string{
	"string": "string", "text": "string", "integer": "int64", "int": "int64",
	"float": "float64", "decimal": "float64", "boolean": "bool", "bool": "bool",
	"datetime": "time.Time", "date": "string", "uuid": "string", "json": "string",
}

var goGormColumnTypes = map[string]string{
	"string": "varchar(255)", "text": "text", "integer": "integer", "int": "integer",
	"float": "double precision", "decimal": "numeric(18,2)", "boolean": "boolean",
	"bool": "boolean", "datetime": "timestamptz", "date": "date", "uuid": "uuid",
	"json": "jsonb",
}

func goFieldDecl(fname string, fspec FieldSpec) string {
	gt := goGormTypes[fspec.Type]
	if gt == "" {
		gt = "string"
	}
	ct := goGormColumnTypes[fspec.Type]
	if ct == "" {
		ct = "varchar(255)"
	}
	tags := []string{`gorm:"column:` + fname + `;type:` + ct}
	if fspec.Required {
		tags[0] += `;not null`
	}
	if fspec.Unique {
		tags[0] += `;uniqueIndex`
	}
	tags[0] += `"`
	fname2 := pascal(fname)
	if gt == "time.Time" {
		// ensure time import hint in struct — handled in template header
	}
	return fmt.Sprintf("\t%s %s `json:\"%s\" %s`", fname2, gt, fname, tags[0])
}

// ─── STAGE 1: ParsePRD ───────────────────────────────────────────────────────

var (
	reSection  = regexp.MustCompile(`(?im)^#{1,3}\s*(?:entities|data models|domain models|entity list)\s*:?\s*$`)
	reEntName  = regexp.MustCompile(`(?m)^[\s\-\*\#]+\*{0,2}([A-Z][a-zA-Z0-9_]{1,40})\*{0,2}[ \t]*(?::|—|-|[ \t]|$)`)
	reField    = regexp.MustCompile(`(?m)^[\s\-\*]+([a-z_][a-z0-9_]{0,40})\s*[:—\-]\s*([a-zA-Z]+)([^\n]*)`)
	reInlField = regexp.MustCompile(`([a-z_][a-z0-9_]{0,40})\s*\(\s*([a-zA-Z]+)([^)]*)`)
	reStory    = regexp.MustCompile(`(?im)As\s+(?:a|an)\s+([^,]+?),\s+I\s+want\s+(?:to\s+)?([^,]+?)(?:,?\s*so\s+that\s+([^.]+))?\.`)
	reSolName  = regexp.MustCompile(`(?m)^#\s+(.+?)\s*$`)
)

var knownIntegrations = map[string]string{
	"stripe": "payments", "auth0": "auth", "clerk": "auth",
	"sendgrid": "email", "twilio": "sms", "datadog": "observability",
	"segment": "analytics", "supabase": "auth",
}

func ParsePRD(text string) Manifest {
	solMatch := reSolName.FindStringSubmatch(text)
	solutionName := "Generated App"
	if len(solMatch) > 1 {
		solutionName = solMatch[1]
	}

	secLoc := reSection.FindStringIndex(text)
	entitySection := ""
	if secLoc != nil {
		rest := text[secLoc[1]:]
		nextH := regexp.MustCompile(`(?m)^#{1,2}\s+\S`).FindStringIndex(rest)
		if nextH != nil {
			entitySection = rest[:nextH[0]]
		} else {
			entitySection = rest
		}
	}

	seen := map[string]bool{}
	entMatches := reEntName.FindAllStringSubmatchIndex(entitySection, -1)
	var entities []Entity
	for i, m := range entMatches {
		ename := entitySection[m[2]:m[3]]
		if seen[ename] {
			continue
		}
		seen[ename] = true
		bodyEnd := len(entitySection)
		if i+1 < len(entMatches) {
			bodyEnd = entMatches[i+1][0]
		}
		body := entitySection[m[1]:bodyEnd]
		fields := map[string]FieldSpec{}
		seenF := map[string]bool{}
		for _, fm := range reField.FindAllStringSubmatch(body, -1) {
			fname := fm[1]
			if seenF[fname] {
				continue
			}
			seenF[fname] = true
			mod := strings.ToLower(fm[3])
			fields[fname] = FieldSpec{
				Type:     strings.ToLower(fm[2]),
				Required: strings.Contains(mod, "required") || strings.Contains(mod, "not null"),
				Unique:   strings.Contains(mod, "unique"),
			}
		}
		if len(fields) == 0 {
			line := entitySection[m[0]:m[1]] + strings.SplitN(body, "\n", 2)[0]
			for _, im := range reInlField.FindAllStringSubmatch(line, -1) {
				fname := im[1]
				if !seenF[fname] {
					seenF[fname] = true
					mod := strings.ToLower(im[3])
					fields[fname] = FieldSpec{
						Type:     strings.ToLower(im[2]),
						Required: strings.Contains(mod, "required"),
					}
				}
			}
		}
		entities = append(entities, Entity{Name: ename, Fields: fields})
	}

	var stories []UserStory
	for _, sm := range reStory.FindAllStringSubmatch(text, -1) {
		s := UserStory{AsA: strings.TrimSpace(sm[1]), IWant: strings.TrimSpace(sm[2])}
		if len(sm) > 3 {
			s.SoThat = strings.TrimSpace(sm[3])
		}
		stories = append(stories, s)
	}

	low := strings.ToLower(text)
	var integrations []Integration
	for k, cat := range knownIntegrations {
		if strings.Contains(low, k) {
			integrations = append(integrations, Integration{Name: k, Category: cat})
		}
	}

	return Manifest{SolutionName: solutionName, Entities: entities, UserStories: stories, Integrations: integrations}
}

// ─── STAGE 2: ManifestToGenome ───────────────────────────────────────────────

var workflowVerbs = map[string]bool{
	"create": true, "update": true, "delete": true, "approve": true,
	"reject": true, "submit": true, "complete": true, "process": true,
}

func ManifestToGenome(manifest Manifest) Genome {
	entMap := map[string]Entity{}
	for _, ent := range manifest.Entities {
		fields := map[string]FieldSpec{
			"id": {Type: "uuid", Required: true},
		}
		for k, v := range ent.Fields {
			if k == "id" || k == "created_at" || k == "updated_at" {
				continue
			}
			fields[k] = v
		}
		entMap[ent.Name] = Entity{
			Name:        ent.Name,
			Fields:      fields,
			Description: ent.Name + " entity (generated by archiet-microcodegen-go)",
		}
	}

	elements := []ArchiMateElement{
		{Name: manifest.SolutionName, Type: "ApplicationComponent", Description: manifest.SolutionName + " Go API application"},
	}
	for _, ent := range manifest.Entities {
		elements = append(elements, ArchiMateElement{Name: ent.Name, Type: "DataObject", Description: ent.Name + " entity"})
	}
	for _, intg := range manifest.Integrations {
		elements = append(elements, ArchiMateElement{Name: intg.Name, Type: "ApplicationService", Description: "External: " + intg.Name})
	}

	bundleID := snake(manifest.SolutionName)
	return Genome{
		GenomeVersion:     "1.0.0",
		SolutionName:      manifest.SolutionName,
		BundleID:          bundleID,
		Language:          "go-chi",
		Modules:           Modules{Core: CoreModule{ModuleType: "crud", Entities: entMap}},
		UserStories:       manifest.UserStories,
		Integrations:      manifest.Integrations,
		ArchiMateElements: elements,
	}
}

// ─── STAGE 3: RenderGenome ───────────────────────────────────────────────────

// Go model template — {{MODEL_NAME}}, {{TABLE_NAME}}, {{FIELDS}}
const tModel = `package models

import "time"

// {{MODEL_NAME}} — per-tenant entity. UserID scopes every record to its owner.
// Every query MUST include WHERE user_id = ? — no cross-user data leaks.
type {{MODEL_NAME}} struct {
	ID        string    ` + "`" + `json:"id" gorm:"primaryKey;type:uuid;default:gen_random_uuid()"` + "`" + `
	UserID    string    ` + "`" + `json:"user_id" gorm:"column:user_id;not null;index"` + "`" + `
{{FIELDS}}
	CreatedAt time.Time ` + "`" + `json:"created_at"` + "`" + `
	UpdatedAt time.Time ` + "`" + `json:"updated_at"` + "`" + `
}

func ({{MODEL_NAME}}) TableName() string { return "{{TABLE_NAME}}" }
`

// Handler template — {{ENTITY_PASCAL}}, {{ENTITY_SNAKE}}, {{ENTITY_PLURAL}}
const tHandler = `package handlers

import (
	"encoding/json"
	"net/http"

	"{{MODULE_PATH}}/internal/database"
	"{{MODULE_PATH}}/internal/models"
	"{{MODULE_PATH}}/internal/middleware"

	"github.com/go-chi/chi/v5"
)

func {{ENTITY_PASCAL}}Routes(r chi.Router) {
	r.Get("/", List{{ENTITY_PASCAL}})
	r.Post("/", Create{{ENTITY_PASCAL}})
	r.Get("/{id}", Get{{ENTITY_PASCAL}})
	r.Put("/{id}", Update{{ENTITY_PASCAL}})
	r.Delete("/{id}", Delete{{ENTITY_PASCAL}})
}

func List{{ENTITY_PASCAL}}(w http.ResponseWriter, r *http.Request) {
	userID := middleware.UserIDFromContext(r.Context())
	var items []models.{{ENTITY_PASCAL}}
	database.DB.Where("user_id = ?", userID).Find(&items)
	json.NewEncoder(w).Encode(items)
}

func Create{{ENTITY_PASCAL}}(w http.ResponseWriter, r *http.Request) {
	userID := middleware.UserIDFromContext(r.Context())
	var item models.{{ENTITY_PASCAL}}
	if err := json.NewDecoder(r.Body).Decode(&item); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest); return
	}
	item.UserID = userID
	if res := database.DB.Create(&item); res.Error != nil {
		http.Error(w, res.Error.Error(), http.StatusInternalServerError); return
	}
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(item)
}

func Get{{ENTITY_PASCAL}}(w http.ResponseWriter, r *http.Request) {
	userID := middleware.UserIDFromContext(r.Context())
	id := chi.URLParam(r, "id")
	var item models.{{ENTITY_PASCAL}}
	if res := database.DB.Where("id = ? AND user_id = ?", id, userID).First(&item); res.Error != nil {
		http.Error(w, "not found", http.StatusNotFound); return
	}
	json.NewEncoder(w).Encode(item)
}

func Update{{ENTITY_PASCAL}}(w http.ResponseWriter, r *http.Request) {
	userID := middleware.UserIDFromContext(r.Context())
	id := chi.URLParam(r, "id")
	var item models.{{ENTITY_PASCAL}}
	if res := database.DB.Where("id = ? AND user_id = ?", id, userID).First(&item); res.Error != nil {
		http.Error(w, "not found", http.StatusNotFound); return
	}
	var updates models.{{ENTITY_PASCAL}}
	if err := json.NewDecoder(r.Body).Decode(&updates); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest); return
	}
	updates.ID = id; updates.UserID = userID
	database.DB.Save(&updates)
	json.NewEncoder(w).Encode(updates)
}

func Delete{{ENTITY_PASCAL}}(w http.ResponseWriter, r *http.Request) {
	userID := middleware.UserIDFromContext(r.Context())
	id := chi.URLParam(r, "id")
	if res := database.DB.Where("id = ? AND user_id = ?", id, userID).Delete(&models.{{ENTITY_PASCAL}}{}); res.Error != nil {
		http.Error(w, "not found", http.StatusNotFound); return
	}
	w.WriteHeader(http.StatusNoContent)
}
`

const tAuth = `package auth

import (
	"encoding/json"
	"net/http"
	"os"
	"time"

	"{{MODULE_PATH}}/internal/database"
	"{{MODULE_PATH}}/internal/models"

	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"
)

var jwtSecret = []byte(getenv("JWT_SECRET_KEY", "dev-secret-change-in-prod"))

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" { return v }
	return fallback
}

type authRequest struct { Email string ` + "`" + `json:"email"` + "`" + `; Password string ` + "`" + `json:"password"` + "`" + ` }

func setTokenCookie(w http.ResponseWriter, userID, email string) {
	claims := jwt.MapClaims{"sub": userID, "email": email, "exp": time.Now().Add(7 * 24 * time.Hour).Unix()}
	token, _ := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(jwtSecret)
	// JWT stored in httpOnly cookie — never localStorage.
	http.SetCookie(w, &http.Cookie{
		Name: "access_token", Value: token, HttpOnly: true,
		SameSite: http.SameSiteLaxMode, MaxAge: 7 * 86400, Path: "/",
	})
}

func Register(w http.ResponseWriter, r *http.Request) {
	var req authRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest); return
	}
	var existing models.User
	if database.DB.Where("email = ?", req.Email).First(&existing).Error == nil {
		http.Error(w, "email already registered", http.StatusConflict); return
	}
	hash, _ := bcrypt.GenerateFromPassword([]byte(req.Password), 10)
	user := models.User{Email: req.Email, PasswordHash: string(hash)}
	database.DB.Create(&user)
	setTokenCookie(w, user.ID, user.Email)
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"message": "registered"})
}

func Login(w http.ResponseWriter, r *http.Request) {
	var req authRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest); return
	}
	var user models.User
	if database.DB.Where("email = ?", req.Email).First(&user).Error != nil {
		http.Error(w, "invalid credentials", http.StatusUnauthorized); return
	}
	if err := bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(req.Password)); err != nil {
		http.Error(w, "invalid credentials", http.StatusUnauthorized); return
	}
	setTokenCookie(w, user.ID, user.Email)
	json.NewEncoder(w).Encode(map[string]string{"message": "logged in"})
}

func Logout(w http.ResponseWriter, r *http.Request) {
	http.SetCookie(w, &http.Cookie{Name: "access_token", MaxAge: -1, Path: "/"})
	json.NewEncoder(w).Encode(map[string]string{"message": "logged out"})
}

func JWTSecret() []byte { return jwtSecret }
`

const tMiddleware = `package middleware

import (
	"context"
	"net/http"
	"os"

	"github.com/golang-jwt/jwt/v5"
)

type ctxKey string
const userIDKey ctxKey = "userID"

func JWTMiddleware(next http.Handler) http.Handler {
	secret := []byte(getenv("JWT_SECRET_KEY", "dev-secret-change-in-prod"))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cookie, err := r.Cookie("access_token")
		if err != nil { http.Error(w, "unauthorized", http.StatusUnauthorized); return }
		tok, err := jwt.Parse(cookie.Value, func(t *jwt.Token) (interface{}, error) {
			return secret, nil
		})
		if err != nil || !tok.Valid { http.Error(w, "unauthorized", http.StatusUnauthorized); return }
		claims, _ := tok.Claims.(jwt.MapClaims)
		ctx := context.WithValue(r.Context(), userIDKey, claims["sub"].(string))
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func UserIDFromContext(ctx context.Context) string {
	v, _ := ctx.Value(userIDKey).(string); return v
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" { return v }
	return fallback
}
`

const tDatabase = `package database

import (
	"fmt"
	"log"
	"os"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

var DB *gorm.DB

func Connect(models ...interface{}) {
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" { log.Fatal("DATABASE_URL not set") }
	var err error
	DB, err = gorm.Open(postgres.Open(dsn), &gorm.Config{})
	if err != nil { log.Fatalf("database connect failed: %v", err) }
	if err := DB.Exec("CREATE EXTENSION IF NOT EXISTS pgcrypto").Error; err != nil {
		fmt.Println("pgcrypto already enabled or not available:", err)
	}
	if err := DB.AutoMigrate(models...); err != nil {
		log.Fatalf("AutoMigrate failed: %v", err)
	}
	log.Println("Database connected and migrated.")
}
`

const tUserModel = `package models

import "time"

type User struct {
	ID           string    ` + "`" + `json:"id" gorm:"primaryKey;type:uuid;default:gen_random_uuid()"` + "`" + `
	Email        string    ` + "`" + `json:"email" gorm:"uniqueIndex;not null"` + "`" + `
	PasswordHash string    ` + "`" + `json:"-" gorm:"not null"` + "`" + `
	CreatedAt    time.Time ` + "`" + `json:"created_at"` + "`" + `
}

func (User) TableName() string { return "users" }
`

// main.go template — {{MODULE_PATH}}, {{ENTITY_ROUTES}}, {{MODEL_POINTERS}}, {{APP_NAME}}
const tMainGo = `package main

import (
	"fmt"
	"log"
	"net/http"
	"os"

	"{{MODULE_PATH}}/internal/auth"
	"{{MODULE_PATH}}/internal/database"
	"{{MODULE_PATH}}/internal/middleware"
	"{{MODULE_PATH}}/internal/models"
{{HANDLER_IMPORTS}}

	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
)

func main() {
	// Connect and auto-migrate all models
	database.Connect(
		&models.User{},
{{MODEL_POINTERS}}
	)

	r := chi.NewRouter()
	r.Use(chimw.Logger)
	r.Use(chimw.Recoverer)

	// Content-type JSON for all responses
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			next.ServeHTTP(w, r)
		})
	})

	// All API routes are mounted under /api so they match the shared Next.js
	// frontend, which calls /api/auth/* and /api/{entities} (frontend/lib/api.ts
	// uses a relative "/api"-prefixed base proxied to this backend).
	r.Route("/api", func(r chi.Router) {
		// Auth routes — no JWT guard
		r.Post("/auth/register", auth.Register)
		r.Post("/auth/login", auth.Login)
		r.Post("/auth/logout", auth.Logout)

		// Protected routes — JWT httpOnly cookie guard
		r.Group(func(r chi.Router) {
			r.Use(middleware.JWTMiddleware)
{{ENTITY_ROUTES}}
		})
	})

	port := os.Getenv("PORT")
	if port == "" { port = "8080" }
	fmt.Printf("{{APP_NAME}} listening on :%s\n", port)
	log.Fatal(http.ListenAndServe(":"+port, r))
}
`

// go.mod template for generated app — {{MODULE_PATH}}
const tGoMod = `module {{MODULE_PATH}}

go 1.21

require (
	github.com/go-chi/chi/v5 v5.0.12
	github.com/golang-jwt/jwt/v5 v5.2.1
	golang.org/x/crypto v0.21.0
	gorm.io/driver/postgres v1.5.7
	gorm.io/gorm v1.25.8
)
`

func RenderGenome(genome Genome) map[string]string {
	files     := map[string]string{}
	bundleID  := genome.BundleID
	modPath   := "github.com/example/" + bundleID
	jwtSecret := randomHex(24)
	entities  := genome.Modules.Core.Entities

	files["internal/models/user.go"] = strings.ReplaceAll(tUserModel, "{{MODULE_PATH}}", modPath)
	files["internal/auth/auth.go"]   = strings.ReplaceAll(tAuth, "{{MODULE_PATH}}", modPath)
	files["internal/middleware/jwt.go"] = tMiddleware
	files["internal/database/db.go"] = tDatabase

	handlerImports  := []string{}
	entityRoutes    := []string{}
	modelPointers   := []string{}

	for entName, entSpec := range entities {
		entSnake  := snake(entName)
		entPascal := pascal(entName)
		entPlural := plural(entSnake)

		fields := []string{}
		useTime := false
		for fname, fspec := range entSpec.Fields {
			if fname == "id" {
				continue
			}
			decl := goFieldDecl(fname, fspec)
			if strings.Contains(decl, "time.Time") {
				useTime = true
			}
			fields = append(fields, "\t"+strings.TrimSpace(decl))
		}
		_ = useTime
		fieldBlock := strings.Join(fields, "\n")

		modelFile := fill(tModel, map[string]string{
			"MODEL_NAME": entPascal,
			"TABLE_NAME": entPlural,
			"FIELDS":     fieldBlock,
		})
		files["internal/models/"+entSnake+".go"] = modelFile

		handlerFile := fill(tHandler, map[string]string{
			"ENTITY_PASCAL":  entPascal,
			"ENTITY_SNAKE":   entSnake,
			"ENTITY_PLURAL":  entPlural,
			"MODULE_PATH":    modPath,
		})
		files["internal/handlers/"+entSnake+".go"] = handlerFile

		handlerImports = append(handlerImports,
			`	"`+modPath+`/internal/handlers"`,
		)
		entityRoutes = append(entityRoutes,
			`		r.Route("/`+entPlural+`", handlers.`+entPascal+`Routes)`,
		)
		modelPointers = append(modelPointers,
			`		&models.`+entPascal+`{},`,
		)
	}

	files["main.go"] = fill(tMainGo, map[string]string{
		"MODULE_PATH":     modPath,
		"APP_NAME":        genome.SolutionName,
		"HANDLER_IMPORTS": strings.Join(dedupe(handlerImports), "\n"),
		"ENTITY_ROUTES":   strings.Join(entityRoutes, "\n"),
		"MODEL_POINTERS":  strings.Join(modelPointers, "\n"),
	})

	files["go.mod"] = fill(tGoMod, map[string]string{"MODULE_PATH": modPath})
	files["go.sum"]  = "// Run: go mod tidy\n"

	files["docker-compose.yml"] = fill(`services:
  app:
    build: .
    ports: ["8080:8080"]
    environment:
      DATABASE_URL: postgresql://archiet:archiet@db:5432/{{BUNDLE_ID}}
      JWT_SECRET_KEY: {{JWT_SECRET}}
    depends_on:
      db:
        condition: service_healthy
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: archiet
      POSTGRES_PASSWORD: archiet
      POSTGRES_DB: {{BUNDLE_ID}}
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U archiet -d {{BUNDLE_ID}}"]
      interval: 3s
      timeout: 3s
      retries: 20
volumes:
  pgdata:
`, map[string]string{"BUNDLE_ID": bundleID, "JWT_SECRET": jwtSecret})

	files["Dockerfile"] = `FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o server .

FROM alpine:3.19
WORKDIR /app
COPY --from=builder /app/server .
EXPOSE 8080
CMD ["./server"]
`

	files[".env.example"] = "DATABASE_URL=postgresql://archiet:archiet@localhost:5432/" + bundleID + "\n" +
		"JWT_SECRET_KEY=" + jwtSecret + "\nPORT=8080\n"

	files["Makefile"] = `build:
	go build -o server .

test:
	go test ./...

migrate:
	@echo "Migrations run via GORM AutoMigrate on startup."

run:
	go run .
`

	files["GENOME.json"] = func() string { b, _ := json.MarshalIndent(genome, "", "  "); return string(b) }()
	files["ARCHITECTURE.md"] = renderArchMd(genome, entities)
	files["openapi.yaml"]    = renderOpenapi(genome, entities)
	files["README.md"]       = renderReadme(genome, entities)

	return files
}

func dedupe(ss []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range ss {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	return out
}

func renderArchMd(genome Genome, entities map[string]Entity) string {
	sb := strings.Builder{}
	sb.WriteString("# Architecture — " + genome.SolutionName + "\n\n")
	sb.WriteString("Generated by archiet-microcodegen-go · ArchiMate 3.2 element notation\n\n")
	sb.WriteString("## Application Layer\n\n| Element | Type | Description |\n|---------|------|-------------|\n")
	for _, el := range genome.ArchiMateElements {
		sb.WriteString(fmt.Sprintf("| `%s` | %s | %s |\n", el.Name, el.Type, el.Description))
	}
	sb.WriteString("\n## Relationships\n\n```\n  " + genome.SolutionName + " (ApplicationComponent)\n")
	for ename := range entities {
		sb.WriteString("    └── " + ename + " (DataObject)  [Realization]\n")
	}
	sb.WriteString("```\n\nhttps://archiet.com?utm_source=pkg.go.dev&utm_medium=package&utm_campaign=microcodegen-go\n")
	return sb.String()
}

func renderOpenapi(genome Genome, entities map[string]Entity) string {
	sb := strings.Builder{}
	sb.WriteString("openapi: '3.1.0'\ninfo:\n  title: " + genome.SolutionName + " API\n")
	sb.WriteString("  version: 0.1.0\nservers:\n  - url: http://localhost:8080\npaths:\n")
	sb.WriteString("  /api/auth/register:\n    post:\n      tags: [auth]\n      summary: Register (returns httpOnly JWT cookie)\n")
	sb.WriteString("      responses: {'201': {description: Registered}}\n")
	sb.WriteString("  /api/auth/login:\n    post:\n      tags: [auth]\n      summary: Login (returns httpOnly JWT cookie)\n")
	sb.WriteString("      responses: {'200': {description: OK}}\n")
	for entName, _ := range entities {
		entSnake  := snake(entName)
		entPlural := plural(entSnake)
		sb.WriteString(fmt.Sprintf("  /api/%s:\n    get:\n      tags: [%s]\n      security: [{cookieAuth: []}]\n      responses: {'200': {description: List}}\n", entPlural, entName))
		sb.WriteString(fmt.Sprintf("    post:\n      tags: [%s]\n      security: [{cookieAuth: []}]\n      responses: {'201': {description: Created}}\n", entName))
		sb.WriteString(fmt.Sprintf("  /api/%s/{id}:\n    get:\n      tags: [%s]\n      security: [{cookieAuth: []}]\n      parameters: [{in: path, name: id, required: true, schema: {type: string}}]\n      responses: {'200': {description: OK}, '404': {description: Not found}}\n", entPlural, entName))
		sb.WriteString(fmt.Sprintf("    put:\n      tags: [%s]\n      security: [{cookieAuth: []}]\n      parameters: [{in: path, name: id, required: true, schema: {type: string}}]\n      responses: {'200': {description: Updated}}\n", entName))
		sb.WriteString(fmt.Sprintf("    delete:\n      tags: [%s]\n      security: [{cookieAuth: []}]\n      parameters: [{in: path, name: id, required: true, schema: {type: string}}]\n      responses: {'204': {description: Deleted}}\n", entName))
	}
	sb.WriteString("components:\n  securitySchemes:\n    cookieAuth:\n      type: apiKey\n      in: cookie\n      name: access_token\n")
	return sb.String()
}

func renderReadme(genome Genome, entities map[string]Entity) string {
	sb := strings.Builder{}
	sb.WriteString("# " + genome.SolutionName + "\n\nGenerated by archiet-microcodegen-go.\n\n## Quick start\n\n```bash\ncp .env.example .env\ndocker compose up\n```\n\n")
	sb.WriteString("## Entities\n\n")
	for ename, espec := range entities {
		sb.WriteString("- **" + ename + "**: " + espec.Description + "\n")
	}
	sb.WriteString("\n## Stack\n\n- Go 1.21 + chi router\n- GORM + PostgreSQL 16\n- JWT httpOnly cookies (never localStorage)\n- Per-tenant: every query filtered by user_id\n")
	return sb.String()
}

// ─── STAGE 4: Pack ────────────────────────────────────────────────────────────

func Pack(files map[string]string) ([]byte, error) {
	buf := &bytes.Buffer{}
	w := zip.NewWriter(buf)
	for fpath, content := range files {
		f, err := w.Create(fpath)
		if err != nil {
			return nil, fmt.Errorf("zip create %s: %w", fpath, err)
		}
		if _, err := f.Write([]byte(content)); err != nil {
			return nil, fmt.Errorf("zip write %s: %w", fpath, err)
		}
	}
	if err := w.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// ─── CLI ──────────────────────────────────────────────────────────────────────

func main() {
	prdFile := flag.String("prd", "", "Path to PRD Markdown file (required)")
	outDir  := flag.String("out", "", "Write extracted files to this directory (default: write ZIP to stdout or -zip path)")
	zipFile := flag.String("zip", "", "Write ZIP archive to this file")
	flag.Parse()

	if *prdFile == "" {
		fmt.Fprintln(os.Stderr, "archiet-microcodegen-go — PRD text → Go chi app\n")
		fmt.Fprintln(os.Stderr, "Usage:")
		fmt.Fprintln(os.Stderr, "  archiet-microcodegen-go -prd prd.md -out ./myapp/")
		fmt.Fprintln(os.Stderr, "  archiet-microcodegen-go -prd prd.md -zip myapp.zip")
		os.Exit(1)
	}

	data, err := os.ReadFile(*prdFile)
	if err != nil {
		fmt.Fprintln(os.Stderr, "read error:", err)
		os.Exit(1)
	}

	manifest := ParsePRD(string(data))
	genome   := ManifestToGenome(manifest)
	files    := RenderGenome(genome)

	if *outDir != "" {
		if err := os.MkdirAll(*outDir, 0755); err != nil {
			fmt.Fprintln(os.Stderr, err); os.Exit(1)
		}
		for fpath, content := range files {
			full := filepath.Join(*outDir, fpath)
			os.MkdirAll(filepath.Dir(full), 0755)
			os.WriteFile(full, []byte(content), 0644)
		}
		fmt.Fprintf(os.Stderr, "Wrote %d files to %s\n", len(files), *outDir)
		return
	}

	zipBytes, err := Pack(files)
	if err != nil {
		fmt.Fprintln(os.Stderr, err); os.Exit(1)
	}
	if *zipFile != "" {
		os.WriteFile(*zipFile, zipBytes, 0644)
		fmt.Fprintf(os.Stderr, "Wrote %s (%d bytes)\n", *zipFile, len(zipBytes))
	} else {
		os.Stdout.Write(zipBytes)
	}
}
