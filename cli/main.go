package main

import (
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

var DefaultServerURL = ""

type PullResponse struct {
	Username    string            `json:"username"`
	ExpiresAt   string            `json:"expires_at"`
	Env         map[string]string `json:"env"`
	Files       []FileMeta        `json:"files"`
}

type FileMeta struct {
	Filename    string `json:"filename"`
	Size        int64  `json:"size"`
	Description string `json:"description"`
	UpdatedAt   string `json:"updated_at"`
}

func resolveServerURL(explicitServer, token string) string {
	if explicitServer != "" {
		return strings.TrimRight(explicitServer, "/")
	}
	if envURL := os.Getenv("CRED_SERVER_URL"); envURL != "" {
		return strings.TrimRight(envURL, "/")
	}
	// Parse embedded server URL from token format: ctk_<base64url(server_url)>_<secret>
	if strings.HasPrefix(token, "ctk_") {
		rest := strings.TrimPrefix(token, "ctk_")
		parts := strings.SplitN(rest, "_", 2)
		if len(parts) == 2 && parts[0] != "" {
			decoded, err := base64.RawURLEncoding.DecodeString(parts[0])
			if err == nil && (strings.HasPrefix(string(decoded), "http://") || strings.HasPrefix(string(decoded), "https://")) {
				return strings.TrimRight(string(decoded), "/")
			}
		}
	}
	if DefaultServerURL != "" {
		return strings.TrimRight(DefaultServerURL, "/")
	}
	return ""
}

func resolveToken(flagToken string) string {
	if flagToken != "" {
		return flagToken
	}
	return os.Getenv("CRED_TOKEN")
}

func fetchBundle(serverURL, token string) (*PullResponse, error) {
	url := fmt.Sprintf("%s/api/cli/pull", serverURL)
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("X-Cred-Token", token)

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("server returned HTTP %d: %s", resp.StatusCode, string(body))
	}

	var data PullResponse
	if err := json.Unmarshal(body, &data); err != nil {
		return nil, fmt.Errorf("invalid JSON response: %w", err)
	}
	return &data, nil
}

func downloadFile(serverURL, token, filename string) ([]byte, error) {
	reqURL := fmt.Sprintf("%s/api/cli/file/%s", serverURL, url.PathEscape(filename))
	req, err := http.NewRequest("GET", reqURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("X-Cred-Token", token)

	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("download failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("server returned HTTP %d: %s", resp.StatusCode, string(body))
	}
	return body, nil
}

func formatEnv(env map[string]string, format string) string {
	switch strings.ToLower(format) {
	case "json":
		b, _ := json.MarshalIndent(env, "", "  ")
		return string(b) + "\n"
	case "export", "sh", "bash":
		var sb strings.Builder
		for k, v := range env {
			escaped := strings.ReplaceAll(v, "'", "'\"'\"'")
			sb.WriteString(fmt.Sprintf("export %s='%s'\n", k, escaped))
		}
		return sb.String()
	case "ps1", "powershell":
		var sb strings.Builder
		for k, v := range env {
			escaped := strings.ReplaceAll(v, "'", "''")
			sb.WriteString(fmt.Sprintf("$env:%s = '%s'\n", k, escaped))
		}
		return sb.String()
	default: // dotenv
		var sb strings.Builder
		for k, v := range env {
			if strings.ContainsAny(v, " \t\n\"'#") {
				escaped := strings.ReplaceAll(v, "\\", "\\\\")
				escaped = strings.ReplaceAll(escaped, "\"", "\\\"")
				escaped = strings.ReplaceAll(escaped, "\n", "\\n")
				sb.WriteString(fmt.Sprintf("%s=\"%s\"\n", k, escaped))
			} else {
				sb.WriteString(fmt.Sprintf("%s=%s\n", k, v))
			}
		}
		return sb.String()
	}
}

func usage() {
	fmt.Fprintf(os.Stderr, `cred-cli — Blitz Credential Manager CLI for AI Agents & Developers

Usage:
  cred-cli <command> [options]

Commands:
  list                  List available environment variable keys and credential files
  env                   Pull environment variables (all or specific --key)
  file <filename>       Pull a specific credential file (e.g. cookies.txt, config.json)
  pull-all              Pull all environment variables (.env) and credential files into a directory
  run -- <cmd> [args]   Execute a command with all credential environment variables injected
  version               Show CLI version

Options:
  --token <TOKEN>       Temporary token obtained from Remote MCP get_temp_token (or env CRED_TOKEN)
  --server <URL>        Server URL override (auto-detected from token by default)
  --key <KEY>           (env command) Fetch only a single environment variable value
  --format <FMT>        (env command) Output format: dotenv (default), json, export, ps1
  -o <PATH>             Output file path (for env or file command)
  --dir <DIR>           (pull-all command) Target directory (default: current directory)
`)
}

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}

	subcmd := os.Args[1]
	if subcmd == "-h" || subcmd == "--help" || subcmd == "help" {
		usage()
		return
	}
	if subcmd == "version" || subcmd == "--version" || subcmd == "-v" {
		fmt.Println("cred-cli v1.0.0")
		return
	}

	// Separate flags and positional args (before `--`) so flags work in any order
	rawArgs := os.Args[2:]
	var execArgs []string
	if subcmd == "run" || subcmd == "exec" {
		for i, a := range rawArgs {
			if a == "--" {
				execArgs = rawArgs[i+1:]
				rawArgs = rawArgs[:i]
				break
			}
		}
	}

	var flagArgs []string
	var posArgs []string
	valueFlags := map[string]bool{
		"-token": true, "--token": true,
		"-server": true, "--server": true,
		"-key": true, "--key": true,
		"-format": true, "--format": true,
		"-o": true, "--o": true,
		"-name": true, "--name": true,
		"-dir": true, "--dir": true,
	}
	for i := 0; i < len(rawArgs); i++ {
		a := rawArgs[i]
		if strings.HasPrefix(a, "-") {
			flagArgs = append(flagArgs, a)
			if valueFlags[a] && i+1 < len(rawArgs) {
				i++
				flagArgs = append(flagArgs, rawArgs[i])
			}
		} else {
			posArgs = append(posArgs, a)
		}
	}

	fs := flag.NewFlagSet(subcmd, flag.ExitOnError)
	tokenFlag := fs.String("token", "", "Temporary token from MCP")
	serverFlag := fs.String("server", "", "Blitz Credential Manager Server URL")
	keyFlag := fs.String("key", "", "Specific env var key")
	formatFlag := fs.String("format", "dotenv", "Output format: dotenv|json|export|ps1")
	outFlag := fs.String("o", "", "Output file path")
	nameFlag := fs.String("name", "", "Filename to pull (for file command)")
	dirFlag := fs.String("dir", ".", "Target directory for pull-all")
	stdoutFlag := fs.Bool("stdout", false, "Print file content to stdout")
	_ = fs.Parse(flagArgs)

	var positionalFile string
	if len(posArgs) > 0 {
		positionalFile = posArgs[0]
	}
	if positionalFile == "" && *nameFlag != "" {
		positionalFile = *nameFlag
	}

	token := resolveToken(*tokenFlag)
	if token == "" {
		fmt.Fprintln(os.Stderr, "Error: --token <TEMP_TOKEN> (or CRED_TOKEN env var) is required. Call MCP tool 'get_temp_token' first.")
		os.Exit(1)
	}

	serverURL := resolveServerURL(*serverFlag, token)
	if serverURL == "" {
		fmt.Fprintln(os.Stderr, "Error: Could not determine server URL. Pass --server <URL> or use a token issued by Blitz Credential Manager.")
		os.Exit(1)
	}

	switch subcmd {
	case "list", "ls":
		data, err := fetchBundle(serverURL, token)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("User: %s (Token expires: %s)\n\n", data.Username, data.ExpiresAt)
		fmt.Printf("Environment Variables (%d):\n", len(data.Env))
		for k := range data.Env {
			fmt.Printf("  - %s\n", k)
		}
		fmt.Printf("\nCredential Files (%d):\n", len(data.Files))
		for _, f := range data.Files {
			fmt.Printf("  - %s (%d bytes) %s\n", f.Filename, f.Size, f.Description)
		}

	case "env", "pull-env":
		data, err := fetchBundle(serverURL, token)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		if *keyFlag != "" {
			val, ok := data.Env[*keyFlag]
			if !ok {
				fmt.Fprintf(os.Stderr, "Error: environment variable '%s' not found\n", *keyFlag)
				os.Exit(1)
			}
			if *outFlag != "" {
				if err := os.WriteFile(*outFlag, []byte(val), 0600); err != nil {
					fmt.Fprintf(os.Stderr, "Error writing %s: %v\n", *outFlag, err)
					os.Exit(1)
				}
				fmt.Fprintf(os.Stderr, "Saved %s to %s\n", *keyFlag, *outFlag)
			} else {
				fmt.Print(val)
			}
			return
		}
		output := formatEnv(data.Env, *formatFlag)
		if *outFlag != "" {
			if err := os.WriteFile(*outFlag, []byte(output), 0600); err != nil {
				fmt.Fprintf(os.Stderr, "Error writing %s: %v\n", *outFlag, err)
				os.Exit(1)
			}
			fmt.Fprintf(os.Stderr, "Saved %d environment variables to %s\n", len(data.Env), *outFlag)
		} else {
			fmt.Print(output)
		}

	case "file", "pull-file":
		if positionalFile == "" {
			fmt.Fprintln(os.Stderr, "Error: filename is required. Usage: cred-cli file <filename> --token <TOKEN> [-o <output_path>]")
			os.Exit(1)
		}
		content, err := downloadFile(serverURL, token, positionalFile)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		if *stdoutFlag {
			os.Stdout.Write(content)
			return
		}
		outPath := *outFlag
		if outPath == "" {
			outPath = filepath.Base(positionalFile)
		}
		if dir := filepath.Dir(outPath); dir != "." && dir != "" {
			_ = os.MkdirAll(dir, 0755)
		}
		if err := os.WriteFile(outPath, content, 0600); err != nil {
			fmt.Fprintf(os.Stderr, "Error writing file %s: %v\n", outPath, err)
			os.Exit(1)
		}
		fmt.Printf("Saved credential file '%s' (%d bytes) -> %s\n", positionalFile, len(content), outPath)

	case "pull-all", "pull":
		data, err := fetchBundle(serverURL, token)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		if err := os.MkdirAll(*dirFlag, 0755); err != nil {
			fmt.Fprintf(os.Stderr, "Error creating dir %s: %v\n", *dirFlag, err)
			os.Exit(1)
		}
		if len(data.Env) > 0 {
			envPath := filepath.Join(*dirFlag, ".env")
			if err := os.WriteFile(envPath, []byte(formatEnv(data.Env, "dotenv")), 0600); err != nil {
				fmt.Fprintf(os.Stderr, "Error writing %s: %v\n", envPath, err)
				os.Exit(1)
			}
			fmt.Printf("Saved %d env vars -> %s\n", len(data.Env), envPath)
		}
		for _, f := range data.Files {
			content, err := downloadFile(serverURL, token, f.Filename)
			if err != nil {
				fmt.Fprintf(os.Stderr, "Error downloading %s: %v\n", f.Filename, err)
				os.Exit(1)
			}
			target := filepath.Join(*dirFlag, filepath.Base(f.Filename))
			if err := os.WriteFile(target, content, 0600); err != nil {
				fmt.Fprintf(os.Stderr, "Error writing %s: %v\n", target, err)
				os.Exit(1)
			}
			fmt.Printf("Saved file '%s' (%d bytes) -> %s\n", f.Filename, len(content), target)
		}

	case "run", "exec":
		if len(execArgs) == 0 {
			execArgs = posArgs
		}
		if len(execArgs) == 0 {
			fmt.Fprintln(os.Stderr, "Error: no command specified. Usage: cred-cli run --token <TOKEN> -- <command> [args...]")
			os.Exit(1)
		}
		data, err := fetchBundle(serverURL, token)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
		cmd := exec.Command(execArgs[0], execArgs[1:]...)
		env := os.Environ()
		for k, v := range data.Env {
			env = append(env, fmt.Sprintf("%s=%s", k, v))
		}
		cmd.Env = env
		cmd.Stdin = os.Stdin
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			if exitErr, ok := err.(*exec.ExitError); ok {
				os.Exit(exitErr.ExitCode())
			}
			fmt.Fprintf(os.Stderr, "Error executing command: %v\n", err)
			os.Exit(1)
		}

	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n\n", subcmd)
		usage()
		os.Exit(1)
	}
}
