use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use std::fs::{self, File};
use std::io::Write;
use std::path::Path;
use zip::write::{SimpleFileOptions, ZipWriter};
use zip::{CompressionMethod, DateTime};

pub fn validate_archive_entry_path(path_str: &str) -> Result<(), String> {
    if path_str.is_empty() {
        return Err("Empty archive entry path".to_string());
    }
    if path_str.contains('\0') {
        return Err("Path contains null byte".to_string());
    }
    let normalized = path_str.replace('\\', "/");
    if normalized.starts_with('/') || (normalized.len() >= 2 && normalized.chars().nth(1) == Some(':')) {
        return Err(format!("Absolute path not allowed in archive: {}", path_str));
    }

    for component in normalized.split('/') {
        if component == ".." {
            return Err(format!("Directory traversal attempt with '..' in: {}", path_str));
        }
    }

    Ok(())
}

#[pyfunction]
#[pyo3(signature = (manifest, output_path, date_time=None))]
pub fn build_epub_archive(
    py: Python<'_>,
    manifest: Vec<(String, Vec<u8>)>,
    output_path: String,
    date_time: Option<(u16, u8, u8, u8, u8, u8)>,
) -> PyResult<()> {
    py.allow_threads(|| {
        let out_path = Path::new(&output_path);
        if let Some(parent) = out_path.parent() {
            fs::create_dir_all(parent).map_err(|e| PyIOError::new_err(e.to_string()))?;
        }

        let file = File::create(out_path).map_err(|e| PyIOError::new_err(e.to_string()))?;
        let mut zip = ZipWriter::new(file);

        let mut mimetype_entry: Option<Vec<u8>> = None;
        let mut other_entries = Vec::new();

        for (name, content) in manifest {
            validate_archive_entry_path(&name).map_err(PyValueError::new_err)?;

            if name == "mimetype" {
                mimetype_entry = Some(content);
            } else {
                other_entries.push((name, content));
            }
        }

        let dt_opt = date_time.and_then(|(y, m, d, h, min, s)| {
            DateTime::from_date_and_time(y, m, d, h, min, s).ok()
        });

        // 1. First entry MUST be mimetype stored uncompressed
        let mimetype_bytes = mimetype_entry.unwrap_or_else(|| b"application/epub+zip".to_vec());
        let mut stored_options = SimpleFileOptions::default()
            .compression_method(CompressionMethod::Stored);
        if let Some(dt) = dt_opt {
            stored_options = stored_options.last_modified_time(dt);
        }

        zip.start_file("mimetype", stored_options)
            .map_err(|e| PyIOError::new_err(e.to_string()))?;
        zip.write_all(&mimetype_bytes)
            .map_err(|e| PyIOError::new_err(e.to_string()))?;

        // 2. Write other entries with Deflated compression
        let mut deflated_options = SimpleFileOptions::default()
            .compression_method(CompressionMethod::Deflated);
        if let Some(dt) = dt_opt {
            deflated_options = deflated_options.last_modified_time(dt);
        }

        for (name, content) in other_entries {
            let options = if name.ends_with(".png") || name.ends_with(".jpg") || name.ends_with(".jpeg") {
                stored_options
            } else {
                deflated_options
            };

            zip.start_file(&name, options)
                .map_err(|e| PyIOError::new_err(e.to_string()))?;
            zip.write_all(&content)
                .map_err(|e| PyIOError::new_err(e.to_string()))?;
        }

        zip.finish().map_err(|e| PyIOError::new_err(e.to_string()))?;

        Ok(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_archive_paths() {
        assert!(validate_archive_entry_path("mimetype").is_ok());
        assert!(validate_archive_entry_path("META-INF/container.xml").is_ok());
        assert!(validate_archive_entry_path("EPUB/text/chapter_1.xhtml").is_ok());
        assert!(validate_archive_entry_path("EPUB/images/figure.png").is_ok());
    }

    #[test]
    fn test_invalid_archive_paths() {
        assert!(validate_archive_entry_path("").is_err());
        assert!(validate_archive_entry_path("/absolute/path").is_err());
        assert!(validate_archive_entry_path("C:/windows/path").is_err());
        assert!(validate_archive_entry_path("C:\\windows\\path").is_err());
        assert!(validate_archive_entry_path("path/../traversal").is_err());
        assert!(validate_archive_entry_path("path/\0null").is_err());
    }
}

